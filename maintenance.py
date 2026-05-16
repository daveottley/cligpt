import importlib.metadata
import os
import shutil
import subprocess
import sys


PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
REQUIREMENTS_FILE = os.path.join(PROJECT_ROOT, "requirements.txt")
AUR_HELPERS = ["paru", "yay", "pikaur", "trizen"]

PYTHON_REQUIREMENTS = {
    "openai": "OpenAI API client",
    "rich": "terminal Markdown/panel renderer",
}

SYSTEM_TOOLS = [
    {
        "name": "LibreOffice",
        "commands": ["libreoffice", "soffice"],
        "feature": "Office document conversion for .doc/.docx/.xls/.xlsx/.odt and related formats",
        "packages": {
            "pacman": ["libreoffice-fresh"],
            "apt": ["libreoffice"],
            "dnf": ["libreoffice"],
            "brew": ["libreoffice"],
        },
    },
    {
        "name": "ocrmypdf",
        "commands": ["ocrmypdf"],
        "feature": "high-quality scanned-PDF OCR",
        "packages": {
            "pacman": [],
            "apt": ["ocrmypdf"],
            "dnf": ["ocrmypdf"],
            "brew": ["ocrmypdf"],
        },
        "aur_packages": ["ocrmypdf"],
        "install_notes": {
            "pacman": "ocrmypdf is not in the official Arch/CachyOS pacman repos. Install it from AUR with an AUR helper such as paru or yay.",
        },
    },
    {
        "name": "Tesseract OCR",
        "commands": ["tesseract"],
        "feature": "image OCR and fallback PDF OCR",
        "packages": {
            "pacman": ["tesseract", "tesseract-data-eng"],
            "apt": ["tesseract-ocr", "tesseract-ocr-eng"],
            "dnf": ["tesseract", "tesseract-langpack-eng"],
            "brew": ["tesseract"],
        },
    },
    {
        "name": "Poppler",
        "commands": ["pdftotext", "pdftoppm"],
        "feature": "PDF text extraction and page rasterization",
        "packages": {
            "pacman": ["poppler"],
            "apt": ["poppler-utils"],
            "dnf": ["poppler-utils"],
            "brew": ["poppler"],
        },
    },
    {
        "name": "Ghostscript",
        "commands": ["gs"],
        "feature": "large PDF compression",
        "packages": {
            "pacman": ["ghostscript"],
            "apt": ["ghostscript"],
            "dnf": ["ghostscript"],
            "brew": ["ghostscript"],
        },
    },
    {
        "name": "file",
        "commands": ["file"],
        "feature": "MIME/file-type diagnostics for blobs and images",
        "packages": {
            "pacman": ["file"],
            "apt": ["file"],
            "dnf": ["file"],
            "brew": ["libmagic"],
        },
    },
    {
        "name": "binwalk",
        "commands": ["binwalk"],
        "feature": "embedded-file hints for blob analysis",
        "packages": {
            "pacman": ["binwalk"],
            "apt": ["binwalk"],
            "dnf": ["binwalk"],
            "brew": ["binwalk"],
        },
    },
]


def command_exists(commands):
    return all(shutil.which(command) for command in commands)


def package_version(package):
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def detect_package_manager():
    managers = [
        ("pacman", "pacman"),
        ("apt", "apt-get"),
        ("dnf", "dnf"),
        ("brew", "brew"),
    ]
    for key, command in managers:
        if shutil.which(command):
            return key
    return None


def detect_aur_helper():
    for helper in AUR_HELPERS:
        if shutil.which(helper):
            return helper
    return None


def packages_for_manager(missing_tools, manager):
    packages = []
    seen = set()
    for tool in missing_tools:
        for package in tool["packages"].get(manager, []):
            if package not in seen:
                seen.add(package)
                packages.append(package)
    return packages


def install_commands_for_manager(packages, manager):
    if not packages or not manager:
        return []
    sudo = [] if hasattr(os, "geteuid") and os.geteuid() == 0 else ["sudo"]
    if manager == "pacman":
        return [[*sudo, "pacman", "-S", "--needed", *packages]]
    if manager == "apt":
        return [
            [*sudo, "apt-get", "update"],
            [*sudo, "apt-get", "install", "-y", *packages],
        ]
    if manager == "dnf":
        return [[*sudo, "dnf", "install", "-y", *packages]]
    if manager == "brew":
        return [["brew", "install", *packages]]
    return []


def aur_packages_for_manager(missing_tools, manager):
    if manager != "pacman":
        return []
    packages = []
    seen = set()
    for tool in missing_tools:
        for package in tool.get("aur_packages", []):
            if package not in seen:
                seen.add(package)
                packages.append(package)
    return packages


def aur_install_commands(packages, helper):
    if not packages or not helper:
        return []
    return [[helper, "-S", "--needed", *packages]]


def install_notes_for_manager(missing_tools, manager):
    if not manager:
        return []
    notes = []
    seen = set()
    for tool in missing_tools:
        note = tool.get("install_notes", {}).get(manager)
        if note and note not in seen:
            seen.add(note)
            notes.append(note)
    return notes


def format_command(command):
    return " ".join(command)


def system_install_environment():
    env = os.environ.copy()
    venv_path = env.pop("VIRTUAL_ENV", None)
    for key in ("PYTHONHOME", "PYTHONPATH"):
        env.pop(key, None)

    remove_paths = {
        os.path.join(PROJECT_ROOT, ".venv", "bin"),
    }
    if venv_path:
        remove_paths.add(os.path.join(venv_path, "bin"))

    path_parts = [
        path for path in env.get("PATH", "").split(os.pathsep)
        if path and os.path.abspath(path) not in {os.path.abspath(item) for item in remove_paths}
    ]
    env["PATH"] = os.pathsep.join(path_parts)
    return env


def doctor():
    missing_tools = []
    print("cligpt doctor")
    print()
    print("Python packages:")
    for package, purpose in PYTHON_REQUIREMENTS.items():
        version = package_version(package)
        if version:
            print(f"  {package:<18} OK      {version} - {purpose}")
        else:
            print(f"  {package:<18} missing        - {purpose}")
    api_key = "OK" if os.getenv("OPENAI_API_KEY") else "missing"
    print(f"  {'OPENAI_API_KEY':<18} {api_key}")
    print()
    print("System tools:")
    for tool in SYSTEM_TOOLS:
        installed = command_exists(tool["commands"])
        status = "OK" if installed else "missing"
        commands = ", ".join(tool["commands"])
        print(f"  {tool['name']:<18} {status:<7} ({commands}) - {tool['feature']}")
        if not installed:
            missing_tools.append(tool)
    if missing_tools:
        print()
        print("Degraded functionality:")
        for tool in missing_tools:
            print(f"  - Missing {tool['name']}: {tool['feature']}.")
        manager = detect_package_manager()
        packages = packages_for_manager(missing_tools, manager) if manager else []
        commands = install_commands_for_manager(packages, manager)
        aur_helper = detect_aur_helper() if manager == "pacman" else None
        aur_packages = aur_packages_for_manager(missing_tools, manager)
        aur_commands = aur_install_commands(aur_packages, aur_helper)
        notes = install_notes_for_manager(missing_tools, manager)
        if commands:
            print()
            print("Suggested system install command(s):")
            for command in commands:
                print(f"  {format_command(command)}")
        if aur_commands:
            print()
            print("Suggested AUR install command(s):")
            for command in aur_commands:
                print(f"  {format_command(command)}")
            notes = []
        if notes:
            print()
            print("Manual install note(s):")
            for note in notes:
                print(f"  - {note}")
        if not commands and not aur_commands and not notes:
            print()
            print("No supported package manager was detected; install the missing tools manually.")
    else:
        print()
        print("All optional system tools were found.")
    return 1 if missing_tools else 0


def run_checked(command, *, cwd=PROJECT_ROOT, dry_run=False, system_install=False):
    print(f"$ {format_command(command)}", flush=True)
    if dry_run:
        return
    env = system_install_environment() if system_install else None
    try:
        subprocess.run(command, cwd=cwd, check=True, env=env)
    except subprocess.CalledProcessError as exc:
        print(f"Command failed with exit code {exc.returncode}: {format_command(command)}")
        raise SystemExit(exc.returncode) from None


def confirm_install_command(command):
    try:
        response = input(f"Run this AUR install command now? {format_command(command)} [y/N] ")
    except EOFError:
        return False
    return response.strip().lower() in {"y", "yes"}


def update(skip_git=False, skip_pip=False, install_system=False, dry_run=False):
    if dry_run:
        print("Dry run: commands will be printed but not executed.")
        print()
    if not skip_git and os.path.isdir(os.path.join(PROJECT_ROOT, ".git")):
        run_checked(["git", "pull", "--ff-only"], dry_run=dry_run)
    elif not skip_git:
        print("Skipping git update: project is not a git checkout.")

    if not skip_pip:
        if not os.path.exists(REQUIREMENTS_FILE):
            print("Skipping Python dependency update: requirements.txt not found.")
        else:
            run_checked([sys.executable, "-m", "pip", "install", "-r", REQUIREMENTS_FILE], dry_run=dry_run)

    missing_tools = [tool for tool in SYSTEM_TOOLS if not command_exists(tool["commands"])]
    if missing_tools:
        manager = detect_package_manager()
        packages = packages_for_manager(missing_tools, manager) if manager else []
        commands = install_commands_for_manager(packages, manager)
        aur_helper = detect_aur_helper() if manager == "pacman" else None
        aur_packages = aur_packages_for_manager(missing_tools, manager)
        aur_commands = aur_install_commands(aur_packages, aur_helper)
        notes = install_notes_for_manager(missing_tools, manager)
        if install_system and commands:
            for command in commands:
                run_checked(command, dry_run=dry_run, system_install=True)
        elif commands:
            print()
            print("System tools are missing. Re-run with --system to install them, or run:")
            for command in commands:
                print(f"  {format_command(command)}")
        if install_system and aur_commands:
            for command in aur_commands:
                print()
                print(f"AUR package available: {format_command(command)}")
                print("AUR packages are user-maintained; review the helper prompts before approving.")
                if dry_run:
                    run_checked(command, dry_run=dry_run, system_install=True)
                elif confirm_install_command(command):
                    run_checked(command, dry_run=dry_run, system_install=True)
                else:
                    print("Skipped AUR install.")
            notes = []
        elif aur_commands:
            print()
            print("AUR package(s) are missing. Re-run with --system to install after confirmation, or run:")
            for command in aur_commands:
                print(f"  {format_command(command)}")
            notes = []
        if notes:
            print()
            print("Some missing tools require manual installation:")
            for note in notes:
                print(f"  - {note}")
        if not commands and not aur_commands and not notes:
            print()
            print("System tools are missing, but no supported package manager was detected.")
    print()
    return doctor()
