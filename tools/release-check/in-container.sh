#!/usr/bin/env bash
# One release scenario inside a fresh ubuntu:24.04 container; /release is prepared by run.sh.
set -euo pipefail
scenario=$1

if [ "$(id -u)" = 0 ]; then
    # A desktop machine has a sudo user; everything after this follows the documentation as that user.
    apt-get update -qq && apt-get install -y -qq sudo >/dev/null
    useradd -m -s /bin/bash tester
    echo "tester ALL=(ALL) NOPASSWD:ALL" >/etc/sudoers.d/tester
    exec sudo -u tester -H bash "$0" "$@"
fi

export DEBIAN_FRONTEND=noninteractive QT_QPA_PLATFORM=offscreen
# ~/index stands in for PyPI for ax-devil itself, at every step including plugin refreshes. It holds the candidate;
# the later release is published to it just before the upgrade check. Every dependency still comes from PyPI.
mkdir -p "$HOME/index" && cp /release/dist/* "$HOME/index/"
export UV_FIND_LINKS=$HOME/index PIP_FIND_LINKS=$HOME/index
step() { echo "--- $*"; }
fail() { echo "FAILED: $*" >&2; exit 1; }

# Run a fenced bash block from the documentation exactly as written.
documented() {
    # The first bash block after the heading; python3 may not be installed yet.
    awk -v heading="$2" '$0 == heading {found = 1} found && /^```bash$/ {inside = 1; next}
        inside && /^```$/ {exit} inside {print}' "$1" >"$HOME/step.sh"
    [ -s "$HOME/step.sh" ] || fail "no bash block after $2 in $1"
    # A documented launch would run until closed; check that it starts, then stop it.
    sed -i 's/^uv run ax-devil$/timeout 60 uv run ax-devil || test $? = 124/' "$HOME/step.sh"
    cat "$HOME/step.sh"
    bash -euxo pipefail "$HOME/step.sh"
}

step "Prerequisites from docs/installation.md"
documented /release/repo/docs/installation.md "## Ubuntu 24.04 prerequisites"
export PATH="$HOME/.local/bin:$PATH"

step "Documented example plugin"
mkdir -p "$HOME/example-plugin"
python3 - <<'EOF'
import pathlib, re
guide = pathlib.Path("/release/repo/docs/plugins.md").read_text()
for name, language in (("pyproject.toml", "toml"), ("example_plugin.py", "python")):
    block = re.search(rf"### {re.escape(name)}\n\n```{language}\n(.*?)```", guide, re.DOTALL).group(1)
    (pathlib.Path.home() / "example-plugin" / name).write_text(block)
EOF

ffmpeg -loglevel error -f lavfi -i testsrc=size=640x360:rate=25 -t 4 -c:v libx264 -pix_fmt yuv420p "$HOME/sample.mp4"

# Use the app as a user would, with $app as the documented launch command.
check_app() {
    step "Help, handlers and catalogs"
    $app --help >/dev/null
    $app list-handlers | grep -q CVAT || fail "built-in handlers missing"
    $app catalog check
    $app catalog render --out "$HOME/sheets" --sheet overview
    ls "$HOME"/sheets/*.png >/dev/null || fail "catalog render wrote no images"

    step "Install the documented plugin"
    $app plugins install "$HOME/example-plugin" 2>&1 | tee "$HOME/install.log"
    ! grep -q "warning:" "$HOME/install.log" || fail "warnings while installing a plugin"
    $app plugins list | grep -q example-plugin || fail "plugin not listed"
    plugin_python=$(ls -d "$HOME"/.local/share/ax-devil/plugins-*/current/.venv/bin/python)
    "$plugin_python" -I -c "
from ax_devil.modules.plugin_system import get_payload_decoder
from ax_devil.modules.plugin_system.validate import validate_plugins
validate_plugins(['example-plugin'])
assert get_payload_decoder('EXAMPLE_FRAME').decode({'frame': 42}).time_slice.start == 42
"
    step "Play a local video with plugins loaded"
    status=0
    timeout 20 $app local --video "$HOME/sample.mp4" >"$HOME/play.log" 2>&1 || status=$?
    cat "$HOME/play.log"
    [ $status = 124 ] || fail "app exited with $status instead of playing until the timeout"
    ! grep -qE "Traceback|Plugin installation unavailable|\| ERROR" "$HOME/play.log" || fail "errors while playing"
}

check_upgrade() {
    step "Upgrade the app; plugins follow on the next launch"
    cp /release/dist-next/* "$HOME/index/"
    $upgrade
    $app --help >/dev/null 2>"$HOME/upgrade.log" || { cat "$HOME/upgrade.log"; fail "launch after upgrade"; }
    cat "$HOME/upgrade.log"
    grep -q "updating plugins" "$HOME/upgrade.log" || fail "plugins were not refreshed"
    ! grep -qE "unavailable|warning:" "$HOME/upgrade.log" || fail "plugins unavailable or warnings after upgrade"
    plugin_python=$(ls -d "$HOME"/.local/share/ax-devil/plugins-*/current/.venv/bin/python)
    "$plugin_python" -I -c "import importlib.metadata as m; assert m.version('ax-devil').endswith('.post1')"
    step "Remove all plugins"
    $app plugins remove --all
    $app plugins list | grep -q "No external plugins" || fail "plugins remain"
}

case $scenario in
tool | py310)
    # --find-links stands in for PyPI for the app itself; every dependency comes from PyPI.
    python=$([ "$scenario" = py310 ] && echo "--python 3.10" || true)
    step "uv tool install ax-devil $python"
    uv tool install $python ax-devil
    app=ax-devil
    check_app
    upgrade="uv tool upgrade ax-devil"
    check_upgrade
    ;;
pip)
    step "pip install ax-devil in a virtual environment"
    python3 -m venv "$HOME/venv"
    "$HOME/venv/bin/python" -m pip install -q ax-devil
    app="$HOME/venv/bin/ax-devil"
    check_app
    upgrade="$HOME/venv/bin/python -m pip install -q --upgrade ax-devil"
    check_upgrade
    ;;
source)
    step "Run from source, as in README.md"
    # Developers resolve everything, including ax-devil, from the clone and its lockfile.
    unset UV_FIND_LINKS PIP_FIND_LINKS
    cd "$HOME"
    # A local copy owned by this user stands in for the GitHub repository.
    mkdir -p "$HOME/remote" && cp -r /release/repo "$HOME/remote/ax-devil"
    sed "s#https://github.com/[^ ]*/ax-devil\.git#$HOME/remote/ax-devil#" /release/repo/README.md >"$HOME/README.local.md"
    grep -q "git clone $HOME/remote/ax-devil" "$HOME/README.local.md" || fail "README clone URL was not redirected"
    documented "$HOME/README.local.md" "## Get started"
    cd "$HOME/ax-devil"
    step "Developer checks"
    make check
    make test
    make test-integration
    app="uv run ax-devil"
    check_app
    step "Editable plugin: source edits apply without reinstalling"
    sed -i 's/"Example decoder"/"Edited example decoder"/' "$HOME/example-plugin/example_plugin.py"
    "$plugin_python" -I -c "
from importlib.metadata import entry_points
plugin = next(e for e in entry_points(group='ax_devil.decoder_plugins') if e.name == 'example').load()
assert plugin.display_name() == 'Edited example decoder'
"
    ;;
*) fail "unknown scenario $scenario" ;;
esac
echo "Scenario $scenario passed"
