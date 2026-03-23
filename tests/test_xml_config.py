"""Tests for XML server configuration manipulation."""

import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from magebench.orchestration.xml_config import modify_server_config


def test_modify_server_config():
    source_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<config><server port="1234" secondaryBindPort="1242"/></config>'
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        source = Path(tmpdir) / "config.xml"
        dest = Path(tmpdir) / "config_out.xml"
        source.write_text(source_xml)

        modify_server_config(source, dest, 5000)

        tree = ET.parse(dest)
        server = tree.getroot().find("server")
        assert server is not None
        assert server.get("port") == "5000"
        assert server.get("secondaryBindPort") == "5008"


def test_modify_server_config_missing_element():
    source_xml = '<?xml version="1.0" encoding="UTF-8"?>\n<config><other/></config>'

    with tempfile.TemporaryDirectory() as tmpdir:
        source = Path(tmpdir) / "config.xml"
        dest = Path(tmpdir) / "config_out.xml"
        source.write_text(source_xml)

        with pytest.raises(ValueError, match="server element not found"):
            modify_server_config(source, dest, 5000)
