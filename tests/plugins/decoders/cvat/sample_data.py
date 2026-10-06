from __future__ import annotations

from pathlib import Path

SAMPLE_CVAT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<annotations>
  <meta>
    <task>
      <id>1</id>
      <name>Demo Task</name>
      <size>2</size>
      <mode>annotation</mode>
      <original_size>
        <width>1280</width>
        <height>720</height>
      </original_size>
      <segments>
        <segment>
          <id>0</id>
          <start>0</start>
          <stop>1</stop>
        </segment>
      </segments>
      <labels>
        <label>
          <name>Car</name>
        </label>
        <label>
          <name>Person</name>
        </label>
      </labels>
    </task>
  </meta>
  <track id="10" label="Car">
    <box frame="0" outside="0" occluded="0" keyframe="1" xtl="100" ytl="200" xbr="200" ybr="400" z_order="0">
      <attribute name="color">blue</attribute>
    </box>
    <box frame="1" outside="0" occluded="1" keyframe="0" xtl="120" ytl="220" xbr="210" ybr="410" z_order="1"/>
  </track>
  <track id="11" label="Person">
    <polygon frame="1" points="50,60;80,60;80,90;50,90" occluded="0" keyframe="1" outside="0"/>
  </track>
</annotations>
"""


def write_sample_cvat_xml(path: Path) -> None:
    """Write the sample CVAT XML content to the provided path."""
    path.write_text(SAMPLE_CVAT_XML, encoding="utf-8")
