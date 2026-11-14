"""In a toolbox or distrobox (Fedora Silverblue, Bluefin): what the host starts
enters the container, what keepbest opens in other apps leaves it."""

import sys
import time

import pytest

from keepbest import app, desktop, viewer


@pytest.fixture
def images(tmp_path):
    paths = []
    for name in ("a.png", "b.jpg"):
        path = tmp_path / name
        path.write_bytes(b"x")
        paths.append(path)
    return paths


def test_in_a_toolbox_the_menu_entry_enters_it(toolbox, tmp_path, monkeypatch):
    """The desktop reads the entry on the host, where the container's Python isn't."""
    monkeypatch.setattr(desktop, "_refresh", lambda folder: None)
    shim = tmp_path / "bin" / "keepbest"
    shim.parent.mkdir()
    shim.write_text("")
    monkeypatch.setattr(sys, "argv", [str(shim)])
    entry, _, command = desktop.install()
    assert command == ["/usr/bin/toolbox", "run", "--container", "my box", str(shim)]
    assert f'Exec=/usr/bin/toolbox run --container "my box" {shim} %f\n' in entry.read_text()


def test_in_a_toolbox_the_page_opens_in_the_hosts_browser(toolbox, monkeypatch):
    """A toolbox has no xdg-open: Python's webbrowser asks gio there, which
    finds no browser and still reports success."""
    started = []
    monkeypatch.setattr(toolbox.subprocess, "Popen", lambda argv, **kw: started.append(argv))
    monkeypatch.setattr(toolbox.webbrowser, "open", lambda url: pytest.fail("webbrowser in a container"))
    assert toolbox.open_url("http://127.0.0.1:1/") is True
    assert started == [["flatpak-spawn", "--host", "xdg-open", "http://127.0.0.1:1/"]]


def test_in_a_toolbox_viewers_are_the_hosts(toolbox, fake_command, images, tmp_path, monkeypatch):
    """Flatpak and the default viewer aren't in the container: both go through flatpak-spawn."""
    monkeypatch.setattr(viewer, "_flatpak_state", {})
    log = tmp_path / "host.calls"
    fake_command("flatpak-spawn", script=f"printf '%s\\n' \"$*\" >> '{log}'; [ \"$3\" = info ] && exit 0")
    monkeypatch.setenv("PATH", str(tmp_path / "fakebin"))
    assert viewer.open_image_viewer(images, "identity") == "Identity"
    assert viewer.open_image_viewer(images, "auto") == "the default viewer"
    deadline = time.time() + 5
    while len(log.read_text().splitlines()) < 4 and time.time() < deadline:
        time.sleep(0.05)
    calls = sorted(log.read_text().splitlines())
    assert calls == sorted([
        "--host flatpak info org.gnome.gitlab.YaLTeR.Identity",
        "--host flatpak run --file-forwarding org.gnome.gitlab.YaLTeR.Identity @@ "
        + " ".join(map(str, images)) + " @@",
        *(f"--host xdg-open {p}" for p in images)])


def test_in_a_toolbox_a_media_optimizer_from_the_host_is_named_as_such(toolbox, tmp_path, monkeypatch):
    """The home folder is shared: one installed on the host is on PATH in the
    container, but its Python isn't there."""
    from keepbest import handoff
    launcher = tmp_path / "media-optimizer"
    launcher.write_text("#!/nonexistent/python3\n")
    launcher.chmod(0o755)
    monkeypatch.setattr(handoff, "available", lambda: str(launcher))
    assert handoff.run(tmp_path, lambda line: None, "http://127.0.0.1:1/") == 127
    errors = []
    monkeypatch.setattr(app, "error", errors.append)
    for name in ("banner", "info", "show", "blank", "success"):
        monkeypatch.setattr(app, name, lambda *a, **kw: None)
    monkeypatch.setattr(app, "page_address", lambda: "http://127.0.0.1:1/")
    app.run_optimizer(tmp_path, {"recursive": True, "excluded": []})
    assert errors == ["media-optimizer could not be started. "
                      "It is installed outside this container: install it inside it too."]


def test_on_an_image_based_system_the_jxl_hint_names_a_toolbox(tmp_path, monkeypatch):
    from keepbest import container, core_logic
    (tmp_path / "ostree-booted").touch()
    monkeypatch.setattr(container, "OSTREE_BOOTED", tmp_path / "ostree-booted")
    assert "install keepbest in a toolbox" in core_logic.jxl_install_hint()[1]
    assert "dnf" not in core_logic.jxl_install_hint()[1]
