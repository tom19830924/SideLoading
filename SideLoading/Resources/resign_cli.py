"""One command interface for the terminal and the bundled SwiftUI app."""
import argparse
import getpass
import json
import os
from pathlib import Path
import signal
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import installer
import ipa_resigner


def mapping(value):
    key, separator, path = value.partition("=")
    if not separator or not key or not path:
        raise argparse.ArgumentTypeError("格式必須是 原識別碼=新群組或描述檔路徑")
    return key, path


def unique_mappings(values):
    result = {}
    for key, value in values:
        if key in result:
            raise installer.InstallError(f"重複指定識別碼：{key}")
        result[key] = value
    return result


def parser():
    root = argparse.ArgumentParser(prog="resign_ipa")
    root.add_argument("--json", action="store_true", help="供 GUI 使用的逐行 JSON 事件")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("environment", help="讀取 USB 手機清單")
    inspect = commands.add_parser("inspect", help="檢查 IPA")
    inspect.add_argument("ipa")
    for command in ("resign", "install"):
        child = commands.add_parser(command, description="使用提供的 IPA、P12 與描述檔重簽；原始檔保持不變。")
        child.add_argument("--ipa", required=True)
        child.add_argument("--certificate", "--p12", required=True, help="包含憑證與私鑰的 .p12／.pfx")
        child.add_argument("--profile", required=True, help="主 App 的描述檔")
        child.add_argument("--output", "-o", help="可省略；預設在來源資料夾產生 原檔名-signed.ipa，同名時加編號")
        child.add_argument("--extension-profile", type=mapping, action="append", default=[], metavar="BUNDLE_ID=PATH")
        child.add_argument("--bundle-id", help="留空依描述檔決定；萬用描述檔預設保留原 ID")
        child.add_argument("--app-group", type=mapping, action="append", default=[], metavar="OLD=NEW")
        child.add_argument("--device", required=command == "install", help="裝置 UDID；重簽模式僅檢查描述檔，安裝模式使用 USB")
        child.add_argument("--omit-capability", action="append", default=[], choices=list(installer.OPTIONAL_CAPABILITIES))
        passwords = child.add_mutually_exclusive_group()
        passwords.add_argument("--password-env", metavar="NAME", help="從指定環境變數讀取密碼")
        passwords.add_argument("--password-stdin", action="store_true", help="透過標準輸入讀取密碼，供 GUI 使用")
        child.add_argument("--force", action="store_true", help="覆寫既有輸出；仍禁止覆寫輸入檔")
    return root


def execute(args, progress):
    if args.command == "environment":
        import device_backend
        return device_backend.environment()
    if args.command == "inspect":
        return installer.inspect(args.ipa)
    extensions, groups = unique_mappings(args.extension_profile), unique_mappings(args.app_group)
    password = None
    if args.password_env:
        if args.password_env not in os.environ:
            raise installer.InstallError(f"找不到密碼環境變數：{args.password_env}")
        password = os.environ.pop(args.password_env)
    output = ipa_resigner.output_path(args.ipa, args.output, args.force)
    ipa_resigner.check_paths(args.ipa, args.certificate, args.profile, output, extensions, args.force)
    if args.command == "install":
        import device_backend
        progress("確認 USB 手機連線與許可…", "inspect")
        device_backend.validate_device(args.device)
    if args.password_stdin:
        password = sys.stdin.read()
    elif password is None:
        if not sys.stdin.isatty():
            raise installer.InstallError("非互動模式請指定 --password-env 或 --password-stdin；空密碼也需明確提供。")
        password = getpass.getpass("P12 密碼（若無密碼直接按 Enter）：")
    result = ipa_resigner.resign_ipa(
        args.ipa, args.certificate, args.profile, output, password, extension_profiles=extensions,
        bundle_id=args.bundle_id, app_groups=groups, omitted_capabilities=args.omit_capability,
        device=args.device, force=args.force, on_progress=lambda message: progress(message, "sign"))
    if args.command == "install":
        progress("重簽完成，開始傳送至所選手機…", "install")
        try:
            result["deviceName"] = device_backend.install(
                result["output"], args.device, result["bundleID"], result["minimumOS"],
                lambda message: progress(message, "install"),
                requires_developer_mode=result.get("requiresDeveloperMode", False))
        except Exception as error:
            raise installer.InstallError(f"{error}\n重簽 IPA 已保留：{result['output']}") from error
    return result


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    position = 1 if arguments[:1] == ["--json"] else 0
    if len(arguments) <= position or arguments[position] not in ("resign", "install", "environment", "inspect"):
        arguments.insert(position, "resign")
    root = parser()
    args = root.parse_args(arguments)
    def progress(message, stage):
        if args.json:
            installer.emit("progress", message=message, stage=stage)
        else:
            print(message, file=sys.stderr, flush=True)
    try:
        result = execute(args, progress)
        if args.json:
            installer.emit("result", data=result)
        elif args.command in ("resign", "install"):
            print(f"重簽完成：{result['output']}\nBundle ID：{result['bundleID']}\n描述檔到期：{result['expires']}")
            if "deviceName" in result:
                print("已安裝至：" + result["deviceName"])
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (KeyboardInterrupt, EOFError):
        message, code = "作業已取消。若已開始安裝，請在手機確認實際狀態。", 130
    except Exception as error:
        message, code = str(error), 1
    if args.json:
        installer.emit("error", message=message)
    else:
        print("作業未完成：" + message, file=sys.stderr)
    return code


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, installer.interrupt)
    sys.exit(main())
