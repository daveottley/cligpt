import unittest
from unittest import mock

import maintenance


class MaintenanceTests(unittest.TestCase):
    def test_packages_for_pacman_deduplicates_tools(self):
        missing = [
            tool for tool in maintenance.SYSTEM_TOOLS
            if tool["name"] in {"Tesseract OCR", "Poppler"}
        ]

        packages = maintenance.packages_for_manager(missing, "pacman")

        self.assertIn("tesseract", packages)
        self.assertIn("tesseract-data-eng", packages)
        self.assertIn("poppler", packages)
        self.assertEqual(len(packages), len(set(packages)))

    def test_pacman_packages_do_not_include_unavailable_ocrmypdf(self):
        missing = [
            tool for tool in maintenance.SYSTEM_TOOLS
            if tool["name"] in {"ocrmypdf", "Tesseract OCR"}
        ]

        packages = maintenance.packages_for_manager(missing, "pacman")
        aur_packages = maintenance.aur_packages_for_manager(missing, "pacman")
        notes = maintenance.install_notes_for_manager(missing, "pacman")

        self.assertNotIn("ocrmypdf", packages)
        self.assertIn("tesseract", packages)
        self.assertEqual(aur_packages, ["ocrmypdf"])
        self.assertTrue(any("AUR helper" in note for note in notes))

    def test_aur_install_commands_use_detected_helper(self):
        commands = maintenance.aur_install_commands(["ocrmypdf"], "paru")

        self.assertEqual(commands, [["paru", "-S", "--needed", "ocrmypdf"]])

    def test_install_commands_for_apt_updates_then_installs(self):
        with mock.patch("os.geteuid", return_value=1000):
            commands = maintenance.install_commands_for_manager(
                ["libreoffice", "ocrmypdf"],
                "apt",
            )

        self.assertEqual(commands[0], ["sudo", "apt-get", "update"])
        self.assertEqual(commands[1], ["sudo", "apt-get", "install", "-y", "libreoffice", "ocrmypdf"])

    def test_install_commands_omit_sudo_when_root(self):
        with mock.patch("os.geteuid", return_value=0):
            commands = maintenance.install_commands_for_manager(
                ["libreoffice", "ocrmypdf"],
                "apt",
            )

        self.assertEqual(commands[0], ["apt-get", "update"])
        self.assertEqual(commands[1], ["apt-get", "install", "-y", "libreoffice", "ocrmypdf"])

    def test_doctor_reports_missing_system_tools(self):
        with mock.patch.object(maintenance, "package_version", return_value="1.0"):
            with mock.patch.object(maintenance, "command_exists", return_value=False):
                with mock.patch.object(maintenance, "detect_package_manager", return_value="pacman"):
                    with mock.patch.object(maintenance, "detect_aur_helper", return_value="paru"):
                        with mock.patch("builtins.print") as printed:
                            status = maintenance.doctor()

        output = "\n".join(" ".join(str(part) for part in call.args) for call in printed.call_args_list)
        self.assertEqual(status, 1)
        self.assertIn("Degraded functionality", output)
        self.assertIn("sudo pacman -S --needed", output)
        self.assertIn("Suggested AUR install command", output)
        self.assertIn("paru -S --needed ocrmypdf", output)

    def test_update_without_system_prints_install_command(self):
        with mock.patch.object(maintenance, "run_checked") as run_checked:
            with mock.patch.object(maintenance, "command_exists", return_value=False):
                with mock.patch.object(maintenance, "detect_package_manager", return_value="pacman"):
                    with mock.patch.object(maintenance, "detect_aur_helper", return_value="yay"):
                        with mock.patch.object(maintenance, "doctor", return_value=1):
                            with mock.patch("os.path.isdir", return_value=False):
                                with mock.patch("os.path.exists", return_value=False):
                                    with mock.patch("builtins.print") as printed:
                                        status = maintenance.update()

        output = "\n".join(" ".join(str(part) for part in call.args) for call in printed.call_args_list)
        self.assertEqual(status, 1)
        self.assertIn("Re-run with --system", output)
        self.assertIn("sudo pacman -S --needed", output)
        self.assertIn("AUR package(s) are missing", output)
        self.assertIn("yay -S --needed ocrmypdf", output)
        run_checked.assert_not_called()

    def test_update_system_dry_run_prints_but_does_not_execute(self):
        executed = []

        def fake_run_checked(command, cwd=maintenance.PROJECT_ROOT, dry_run=False):
            executed.append((command, dry_run))

        with mock.patch.object(maintenance, "run_checked", side_effect=fake_run_checked):
            with mock.patch.object(maintenance, "command_exists", return_value=False):
                with mock.patch.object(maintenance, "detect_package_manager", return_value="pacman"):
                    with mock.patch.object(maintenance, "detect_aur_helper", return_value="paru"):
                        with mock.patch.object(maintenance, "doctor", return_value=1):
                            with mock.patch("os.path.isdir", return_value=True):
                                with mock.patch("os.path.exists", return_value=True):
                                    status = maintenance.update(install_system=True, dry_run=True)

        self.assertEqual(status, 1)
        self.assertIn((["git", "pull", "--ff-only"], True), executed)
        self.assertTrue(any(command[:3] == ["sudo", "pacman", "-S"] and dry_run for command, dry_run in executed))
        self.assertFalse(any(command[:3] == ["sudo", "pacman", "-S"] and "ocrmypdf" in command for command, _ in executed))
        self.assertIn((["paru", "-S", "--needed", "ocrmypdf"], True), executed)

    def test_update_system_requires_confirmation_before_aur_install(self):
        executed = []

        def fake_run_checked(command, cwd=maintenance.PROJECT_ROOT, dry_run=False):
            executed.append(command)

        with mock.patch.object(maintenance, "run_checked", side_effect=fake_run_checked):
            with mock.patch.object(maintenance, "command_exists", return_value=False):
                with mock.patch.object(maintenance, "detect_package_manager", return_value="pacman"):
                    with mock.patch.object(maintenance, "detect_aur_helper", return_value="paru"):
                        with mock.patch.object(maintenance, "confirm_install_command", return_value=False):
                            with mock.patch.object(maintenance, "doctor", return_value=1):
                                with mock.patch("os.path.isdir", return_value=False):
                                    with mock.patch("os.path.exists", return_value=False):
                                        maintenance.update(install_system=True)

        self.assertTrue(any(command[:3] == ["sudo", "pacman", "-S"] for command in executed))
        self.assertNotIn(["paru", "-S", "--needed", "ocrmypdf"], executed)

    def test_update_system_runs_aur_install_after_confirmation(self):
        executed = []

        def fake_run_checked(command, cwd=maintenance.PROJECT_ROOT, dry_run=False):
            executed.append(command)

        with mock.patch.object(maintenance, "run_checked", side_effect=fake_run_checked):
            with mock.patch.object(maintenance, "command_exists", return_value=False):
                with mock.patch.object(maintenance, "detect_package_manager", return_value="pacman"):
                    with mock.patch.object(maintenance, "detect_aur_helper", return_value="paru"):
                        with mock.patch.object(maintenance, "confirm_install_command", return_value=True):
                            with mock.patch.object(maintenance, "doctor", return_value=1):
                                with mock.patch("os.path.isdir", return_value=False):
                                    with mock.patch("os.path.exists", return_value=False):
                                        maintenance.update(install_system=True)

        self.assertIn(["paru", "-S", "--needed", "ocrmypdf"], executed)


if __name__ == "__main__":
    unittest.main()
