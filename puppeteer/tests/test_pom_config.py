import xml.etree.ElementTree as ET
from pathlib import Path

MAVEN_NS = {"m": "http://maven.apache.org/POM/4.0.0"}
ROOT_POM = Path(__file__).resolve().parents[2] / "pom.xml"


def test_root_compiler_uses_incremental_compilation():
    tree = ET.parse(ROOT_POM)
    plugin = tree.find(
        "./m:build/m:pluginManagement/m:plugins/m:plugin[m:artifactId='maven-compiler-plugin']",
        MAVEN_NS,
    )
    assert plugin is not None

    use_incremental = plugin.find("./m:configuration/m:useIncrementalCompilation", MAVEN_NS)
    assert use_incremental is not None
    assert use_incremental.text == "true"
