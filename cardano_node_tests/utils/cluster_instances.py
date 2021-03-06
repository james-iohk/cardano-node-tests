"""Functionality for setting up new DevOps cluster instance."""
import logging
import os
import re
from pathlib import Path
from typing import List
from typing import NamedTuple

from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.types import FileType

LOGGER = logging.getLogger(__name__)

CLUSTER_ERA = os.environ.get("CLUSTER_ERA") or "shelley"
if CLUSTER_ERA not in ("shelley", "allegra", "mary"):
    raise RuntimeError(f"Invalid CLUSTER_ERA`: `{CLUSTER_ERA}")

TX_ERA = os.environ.get("TX_ERA") or ""
if TX_ERA not in ("", "shelley", "allegra", "mary"):
    raise RuntimeError(f"Invalid TX_ERA`: `{TX_ERA}")

SCRIPTS_DIR = Path(__file__).parent.parent / "cluster_scripts" / CLUSTER_ERA


class InstanceFiles(NamedTuple):
    start_script: Path
    stop_script: Path
    dir: Path


class InstancePorts(NamedTuple):
    webserver: int
    bft1: int
    bft2: int
    bft3: int
    pool1: int
    pool2: int
    supervisor: int
    ekg: int
    prometheus: int


def get_instance_ports(instance_num: int) -> InstancePorts:
    """Return ports mapping for given cluster instance."""
    offset = (50 + instance_num) * 10
    base = 30000 + offset

    pools_offset = 0 if CLUSTER_ERA == "shelley" else 2

    ports = InstancePorts(
        webserver=base,
        bft1=base + 1,
        bft2=base + 2 if pools_offset else 0,
        bft3=base + 3 if pools_offset else 0,
        pool1=base + 2 + pools_offset,
        pool2=base + 3 + pools_offset,
        supervisor=12001 + instance_num,
        ekg=12588 + instance_num,
        prometheus=12898 + instance_num,
    )
    return ports


def _get_nix_paths(infile: Path, search_str: str) -> List[Path]:
    """Return path of nix files."""
    with open(infile) as in_fp:
        content = in_fp.read()

    nix_files = re.findall(f"/nix/store/({search_str})", content)
    nix_store = Path("/nix/store")
    nix_paths = [nix_store / c for c in nix_files]

    return nix_paths


def _reconfigure_nix_file(infile: Path, destdir: Path, instance_num: int) -> Path:
    """Reconfigure scripts and config files located on nix.

    Recursive function.
    """
    fname = infile.name

    with open(infile) as in_fp:
        content = in_fp.read()

    instance_ports = get_instance_ports(instance_num)
    new_content = content.replace("/state-cluster", f"/state-cluster{instance_num}")
    # replace node port number strings, omitting the last digit
    new_content = new_content.replace("3000", str(instance_ports.webserver // 10))
    new_content = new_content.replace("9001", str(instance_ports.supervisor))
    new_content = new_content.replace(
        "supervisorctl ", f"supervisorctl -s http://127.0.0.1:{instance_ports.supervisor} "
    )

    if fname == "start-cluster":
        # reconfigure pool margin
        new_content = new_content.replace('"$(jq -n $POOL_MARGIN_NUM/10)"', "0.35")
        # reconfigure pool cost
        new_content = new_content.replace('"$(($RANDOM % 100000000))"', "600")

        # reconfigure path to supervisor config
        supervisor_conf = _get_nix_paths(infile, r"[^/]+-supervisor\.conf")[0]
        dest_supervisor_conf = (destdir / supervisor_conf.name).resolve()
        new_content = new_content.replace(str(supervisor_conf), str(dest_supervisor_conf))
        _reconfigure_nix_file(infile=supervisor_conf, destdir=destdir, instance_num=instance_num)

        # reconfigure path to node.json file
        _node_json = re.search(r"cp (.*node\.json) \./state-cluster[0-9]?/config.json", new_content)
        if not _node_json:
            raise RuntimeError("Failed to find `node.json` in the `start-cluster` script.")
        node_json = Path(_node_json.group(1))
        dest_node_json = (destdir / node_json.name).resolve()
        new_content = new_content.replace(str(node_json), str(dest_node_json))
        _reconfigure_nix_file(infile=node_json, destdir=destdir, instance_num=instance_num)
    elif fname.endswith("-supervisor.conf"):
        # reconfigure supervisor settings
        node_commands = _get_nix_paths(infile, r"[^/]+-cardano-node")
        for ncmd in node_commands:
            dest_ncmd = (destdir / ncmd.name).resolve()
            new_content = new_content.replace(str(ncmd), str(dest_ncmd))
            _reconfigure_nix_file(infile=ncmd, destdir=destdir, instance_num=instance_num)
    elif fname.endswith("-cardano-node"):
        # reconfigure path to topology config file
        topology_yaml = _get_nix_paths(infile, r"[^/]+-topology\.yaml")[0]
        dest_topology_yaml = (destdir / topology_yaml.name).resolve()
        new_content = new_content.replace(str(topology_yaml), str(dest_topology_yaml))
        # reconfigure ports in topology config
        _reconfigure_nix_file(infile=topology_yaml, destdir=destdir, instance_num=instance_num)
    elif fname.endswith("node.json"):
        # reconfigure metrics ports in node.json
        new_content = new_content.replace("12788", str(instance_ports.ekg))
        new_content = new_content.replace("12798", str(instance_ports.prometheus))

    dest_file = destdir / fname
    with open(dest_file, "w") as out_fp:
        out_fp.write(new_content)

    # make files without extension executable
    if "." not in fname:
        dest_file.chmod(0o755)

    return dest_file


def _reconfigure_local(indir: Path, destdir: Path, instance_num: int) -> None:
    """Reconfigure scripts and config files located in this repository."""
    for infile in indir.glob("*"):
        fname = infile.name

        with open(infile) as in_fp:
            content = in_fp.read()

        instance_ports = get_instance_ports(instance_num)
        new_content = content.replace("/state-cluster", f"/state-cluster{instance_num}")
        # replace node port number strings, omitting the last digit
        new_content = new_content.replace("3000", str(instance_ports.webserver // 10))
        new_content = new_content.replace("9001", str(instance_ports.supervisor))
        new_content = new_content.replace(
            "supervisorctl ", f"supervisorctl -s http://127.0.0.1:{instance_ports.supervisor} "
        )

        if fname.endswith("node.json"):
            # reconfigure metrics ports in node.json
            new_content = new_content.replace("12788", str(instance_ports.ekg))
            new_content = new_content.replace("12798", str(instance_ports.prometheus))

        dest_file = destdir / fname
        with open(dest_file, "w") as out_fp:
            out_fp.write(new_content)

        # make files without extension executable
        if "." not in fname:
            dest_file.chmod(0o755)


def _get_cardano_node_socket_path(instance_num: int) -> Path:
    """Return path to socket file in the given cluster instance."""
    socket_path = Path(os.environ["CARDANO_NODE_SOCKET_PATH"]).resolve()
    state_cluster_dirname = f"state-cluster{instance_num}"
    state_cluster = socket_path.parent.parent / state_cluster_dirname
    new_socket_path = state_cluster / socket_path.name
    return new_socket_path


def set_cardano_node_socket_path(instance_num: int) -> None:
    """Set the `CARDANO_NODE_SOCKET_PATH` env variable for the given cluster instance."""
    socket_path = _get_cardano_node_socket_path(instance_num)
    os.environ["CARDANO_NODE_SOCKET_PATH"] = str(socket_path)


def prepare_files(
    destdir: FileType,
    instance_num: int,
    start_script: FileType = "",
    stop_script: FileType = "",
) -> InstanceFiles:
    """Prepare files for starting and stoping cluster instance."""
    destdir = Path(destdir).expanduser().resolve()

    if start_script:
        start_script = Path(start_script)
    elif CLUSTER_ERA == "shelley":
        start_script = Path(helpers.get_cmd_path("start-cluster"))
    else:
        start_script = SCRIPTS_DIR / "start-cluster-hfc"

    if stop_script:
        stop_script = Path(stop_script)
    elif CLUSTER_ERA == "shelley":
        stop_script = Path(helpers.get_cmd_path("stop-cluster"))
    else:
        stop_script = SCRIPTS_DIR / "stop-cluster-hfc"

    start_script = start_script.expanduser().resolve()
    stop_script = stop_script.expanduser().resolve()

    if CLUSTER_ERA == "shelley":
        new_start_script = _reconfigure_nix_file(
            infile=start_script, destdir=destdir, instance_num=instance_num
        )
        new_stop_script = _reconfigure_nix_file(
            infile=stop_script, destdir=destdir, instance_num=instance_num
        )
    else:
        _reconfigure_local(indir=start_script.parent, destdir=destdir, instance_num=instance_num)
        new_start_script = destdir / start_script.name
        new_stop_script = destdir / stop_script.name

    return InstanceFiles(start_script=new_start_script, stop_script=new_stop_script, dir=destdir)
