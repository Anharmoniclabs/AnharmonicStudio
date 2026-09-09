"""Copy only the three public confirmation-page assets for Cloudflare deployment."""

from pathlib import Path
import shutil

root = Path(__file__).resolve().parent
public = root / "public"
(public / "assets").mkdir(parents=True, exist_ok=True)
shutil.copyfile(root.parent / "templates/success.html", public / "success.html")
for name in ("success.js", "success.css"):
    shutil.copyfile(root.parent / "assets" / name, public / "assets" / name)
expected = {"success.html", "assets/success.js", "assets/success.css"}
actual = {str(path.relative_to(public)) for path in public.rglob("*") if path.is_file()}
if actual != expected or any(path.is_symlink() for path in public.rglob("*")):
    raise SystemExit("Unexpected file in Cloudflare public assets; refusing deployment")
print("Prepared confirmation page assets; installers remain in private R2 storage.")
