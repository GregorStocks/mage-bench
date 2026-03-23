"""Server XML configuration manipulation."""

import xml.etree.ElementTree as ET
from pathlib import Path


def modify_server_config(source: Path, destination: Path, port: int) -> None:
    """
    Copy and modify the server config.xml.
    Sets the primary port and secondary bind port (port + 8).
    """
    tree = ET.parse(source)
    root = tree.getroot()

    server_elem = root.find("server")
    if server_elem is None:
        raise ValueError("server element not found in config")

    server_elem.set("port", str(port))
    server_elem.set("secondaryBindPort", str(port + 8))

    tree.write(destination, encoding="UTF-8", xml_declaration=True)
