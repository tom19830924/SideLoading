"""Ad-hoc sign embedded runtime Mach-O files before Xcode signs the outer app."""
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1])
magic = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce",
         b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"}
for path in sorted(root.rglob("*")):
    if path.is_file() and not path.is_symlink():
        with path.open("rb") as stream:
            is_macho = stream.read(4) in magic
        if is_macho:
            subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(path)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
