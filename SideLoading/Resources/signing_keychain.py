"""Import a PKCS#12 identity without putting its password in process arguments.

Security.framework receives the password in memory and an explicit temporary
keychain. No import targets the login keychain or changes the default keychain.
"""
import ctypes as C
from contextlib import contextmanager
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import threading

from installer import InstallError, run


class _Security:
    def __init__(self):
        self.cf = C.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.security = C.CDLL("/System/Library/Frameworks/Security.framework/Security")
        self.references = []
        pointer = C.c_void_p
        self._bind(self.cf, "CFRelease", None, [pointer])
        self._bind(self.cf, "CFStringCreateWithBytes", pointer, [pointer, pointer, C.c_long, C.c_uint32, C.c_ubyte])
        self._bind(self.cf, "CFDataCreate", pointer, [pointer, pointer, C.c_long])
        self._bind(self.cf, "CFArrayCreate", pointer, [pointer, pointer, C.c_long, pointer])
        self._bind(self.cf, "CFArrayGetCount", C.c_long, [pointer])
        self._bind(self.cf, "CFArrayGetValueAtIndex", pointer, [pointer, C.c_long])
        self._bind(self.cf, "CFDictionaryCreate", pointer, [pointer, pointer, pointer, C.c_long, pointer, pointer])
        self._bind(self.security, "SecKeychainCreate", C.c_int32,
                   [C.c_char_p, C.c_uint32, pointer, C.c_ubyte, pointer, C.POINTER(pointer)])
        self._bind(self.security, "SecKeychainDelete", C.c_int32, [pointer])
        self._bind(self.security, "SecKeychainCopySearchList", C.c_int32, [C.POINTER(pointer)])
        self._bind(self.security, "SecKeychainSetSearchList", C.c_int32, [pointer])
        self._bind(self.security, "SecKeychainUnlock", C.c_int32, [pointer, C.c_uint32, pointer, C.c_ubyte])
        self._bind(self.security, "SecTrustedApplicationCreateFromPath", C.c_int32, [C.c_char_p, C.POINTER(pointer)])
        self._bind(self.security, "SecAccessCreate", C.c_int32, [pointer, pointer, C.POINTER(pointer)])
        self._bind(self.security, "SecPKCS12Import", C.c_int32, [pointer, pointer, C.POINTER(pointer)])

    @staticmethod
    def _bind(library, name, result, arguments):
        function = getattr(library, name)
        function.restype, function.argtypes = result, arguments

    def retain_result(self, reference):
        value = reference.value if isinstance(reference, C.c_void_p) else reference
        if not value:
            raise InstallError("無法配置簽署所需的系統資源。")
        self.references.append(value)
        return value

    def string(self, value):
        data = value.encode("utf-8")
        return self.retain_result(self.cf.CFStringCreateWithBytes(None, data, len(data), 0x08000100, False))

    def data(self, value):
        return self.retain_result(self.cf.CFDataCreate(None, value, len(value)))

    def callbacks(self, name):
        return C.addressof(C.c_byte.in_dll(self.cf, name))

    def array(self, values):
        pointers = (C.c_void_p * len(values))(*values)
        return self.retain_result(self.cf.CFArrayCreate(None, pointers, len(values), self.callbacks("kCFTypeArrayCallBacks")))

    def dictionary(self, values):
        keys = (C.c_void_p * len(values))(*(C.c_void_p.in_dll(self.security, key).value for key in values))
        pointers = (C.c_void_p * len(values))(*values.values())
        return self.retain_result(self.cf.CFDictionaryCreate(
            None, keys, pointers, len(values), self.callbacks("kCFTypeDictionaryKeyCallBacks"),
            self.callbacks("kCFTypeDictionaryValueCallBacks")))

    def close(self):
        for reference in reversed(self.references):
            self.cf.CFRelease(reference)
        self.references.clear()


def _check(status, message):
    if status:
        raise InstallError(f"{message}（macOS 狀態碼 {status}）。")


@contextmanager
def _password_pipe(password):
    read_fd, write_fd = os.pipe()
    def write_password():
        try:
            remaining = memoryview((password + "\n").encode("utf-8"))
            while remaining:
                remaining = remaining[os.write(write_fd, remaining):]
        except OSError:
            pass  # The consumer may have failed or been cancelled.
        finally:
            os.close(write_fd)
    writer = threading.Thread(target=write_password, daemon=True)
    writer.start()
    try:
        yield read_fd
    finally:
        os.close(read_fd)
        writer.join(timeout=1)


def _compatible_pkcs12(path, password, work):
    """Normalize legacy password encodings in memory, never in a PEM file.

    macOS can reject empty/Unicode P12 passwords that OpenSSL can read. Only
    retry an authentication failure, with the exact password already supplied.
    These commands must not use run(check=True): stdout can contain a private
    key, so failures must never incorporate captured output into an exception.
    """
    message = "無法匯入 .p12／.pfx，請確認密碼、檔案內容及私鑰。"
    if "\n" in password or "\r" in password:
        raise InstallError(message)
    with _password_pipe(password) as password_fd:
        pem, _, status = run(["/usr/bin/openssl", "pkcs12", "-in", path, "-passin", f"fd:{password_fd}", "-nodes"],
                             check=False, pass_fds=(password_fd,))
    if status or b"PRIVATE KEY" not in pem:
        raise InstallError(message)
    certificates = re.findall(rb"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", pem, re.S)
    if not certificates or len(re.findall(rb"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----", pem)) != 1:
        raise InstallError(message)
    replacement_password = secrets.token_urlsafe(32)
    try:
        # OpenSSL needs separate certificate/key inputs on non-seekable stdin.
        # Only public certificates reach this temporary file; the key is piped.
        with tempfile.NamedTemporaryFile(dir=work, prefix="public-certificates-", suffix=".pem") as public_file:
            public_file.write(b"\n".join(certificates))
            public_file.flush()
            with _password_pipe(replacement_password) as password_fd:
                normalized, _, status = run(["/usr/bin/openssl", "pkcs12", "-export", "-in", public_file.name,
                                            "-inkey", "/dev/stdin", "-passout", f"fd:{password_fd}"],
                                            input_data=pem, check=False, pass_fds=(password_fd,))
    finally:
        pem = None
    if status:
        raise InstallError(message)
    return normalized, replacement_password


class SigningKeychain:
    """Own the imported private key for one operation, including failure cleanup."""
    def __init__(self, path, certificate, password):
        self.path = Path(path)
        self.certificate = Path(certificate)
        self.password = password
        self.api = None
        self.keychain = C.c_void_p()

    def __enter__(self):
        if sys.platform != "darwin":
            raise InstallError("此重簽工具需要 macOS 的 codesign 與鑰匙圈服務。")
        self.api = _Security()
        temporary_password = secrets.token_urlsafe(32)
        encoded = temporary_password.encode("utf-8")
        try:
            _check(self.api.security.SecKeychainCreate(
                bytes(self.path), len(encoded), encoded, False, None, C.byref(self.keychain)), "無法建立暫存鑰匙圈")
            self.api.retain_result(self.keychain)
            _check(self.api.security.SecKeychainUnlock(self.keychain, len(encoded), encoded, True), "無法解鎖暫存鑰匙圈")
            run(["/usr/bin/security", "set-keychain-settings", self.path])
            trusted, access, items = C.c_void_p(), C.c_void_p(), C.c_void_p()
            _check(self.api.security.SecTrustedApplicationCreateFromPath(b"/usr/bin/codesign", C.byref(trusted)), "無法設定簽署工具存取權")
            self.api.retain_result(trusted)
            _check(self.api.security.SecAccessCreate(
                self.api.string("SideLoading temporary signing key"), self.api.array([trusted.value]), C.byref(access)), "無法設定私鑰存取權")
            self.api.retain_result(access)
            import_options = {
                "kSecImportExportPassphrase": self.api.string(self.password),
                "kSecImportExportKeychain": self.keychain.value,
                "kSecImportExportAccess": access.value,
            }
            options = self.api.dictionary(import_options)
            status = self.api.security.SecPKCS12Import(self.api.data(self.certificate.read_bytes()), options, C.byref(items))
            if items.value:
                self.api.retain_result(items)
            if status == -25293:
                normalized, compatible_password = _compatible_pkcs12(self.certificate, self.password, self.path.parent)
                import_options["kSecImportExportPassphrase"] = self.api.string(compatible_password)
                items = C.c_void_p()
                status = self.api.security.SecPKCS12Import(
                    self.api.data(normalized), self.api.dictionary(import_options), C.byref(items))
                if items.value:
                    self.api.retain_result(items)
            _check(status, "無法匯入 .p12／.pfx，請確認密碼、檔案內容及私鑰")
            # This is a disposable random keychain password, never the P12 password.
            # Limit noninteractive signing to Apple's tools in this keychain only.
            run(["/usr/bin/security", "set-key-partition-list", "-S", "apple-tool:,apple:",
                 "-s", "-k", temporary_password, self.path])
            # codesign also resolves certificate chains through the search
            # list, even with --keychain. Delete removes only our own entry.
            search_list = C.c_void_p()
            _check(self.api.security.SecKeychainCopySearchList(C.byref(search_list)), "無法讀取鑰匙圈搜尋清單")
            self.api.retain_result(search_list)
            entries = [self.api.cf.CFArrayGetValueAtIndex(search_list, index)
                       for index in range(self.api.cf.CFArrayGetCount(search_list))]
            _check(self.api.security.SecKeychainSetSearchList(self.api.array(entries + [self.keychain.value])), "無法登記暫存鑰匙圈")
            return self
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise
        finally:
            self.password = None

    def __exit__(self, error_type, error, traceback):
        status = 0
        if self.api:
            if self.keychain.value:
                status = self.api.security.SecKeychainDelete(self.keychain)
                self.keychain = C.c_void_p()
            self.api.close()
        if status:
            message = f"無法移除暫存鑰匙圈（{status}），請手動移除：{self.path}"
            if error_type is None:
                raise InstallError(message)
            print(message, file=sys.stderr)
