from __future__ import annotations

from ax_devil.modules.filtering import FilterState
from ax_devil.modules.scene.filtering import filter_scene
from ax_devil.plugins.decoders.mot.decoder import build_mot_filter_config, decode_mot_frame, prepare_mot_frame_payloads


def test_each_mot_class_has_exactly_one_toggle() -> None:
    """Every MOT Challenge class id, and an unknown id, can be hidden without hiding any other class."""
    config = build_mot_filter_config()
    for class_id in (*range(1, 14), 99):
        payloads, _ = prepare_mot_frame_payloads([f"1,1,0,0,64,48,1,{class_id},1.0"], width=640, height=480)
        scene = decode_mot_frame(payloads[0])
        assert filter_scene(scene, config, FilterState(config)).entities, class_id

        hiding = []
        for option in config.options:
            state = FilterState(config)
            state.set_enabled(option.id, False)
            if not filter_scene(scene, config, state).entities:
                hiding.append(option.id)

        assert len(hiding) == 1, (class_id, hiding)
