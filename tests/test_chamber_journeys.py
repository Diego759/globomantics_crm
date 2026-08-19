"""Journeys are data; these tests are the schema check the data never gets."""

from __future__ import annotations

import json

import pytest

from local_mind_monitor.bands import BANDS
from local_mind_monitor.chamber import journeys as J


def test_band_for_beat_uses_the_analyser_band_edges():
    assert J.band_for_beat(2.5) == "Delta"
    assert J.band_for_beat(6.0) == "Theta"
    assert J.band_for_beat(10.0) == "Alpha"
    assert J.band_for_beat(18.0) == "Beta"
    assert J.band_for_beat(40.0) == "Gamma"
    assert J.band_for_beat(0.4) == ""      # below delta: no band to score against
    assert J.band_for_beat(80.0) == ""


def test_duration_is_the_sum_of_its_segments():
    j = J.get("power-nap")
    assert j.seconds == sum(s.seconds for s in j.segments)
    assert j.minutes == pytest.approx(20.0)


def test_frequencies_glide_linearly_within_a_segment():
    j = J.get("first-descent")
    descend_start = 4 * 60
    a = j.at(descend_start)
    mid = j.at(descend_start + 2.5 * 60)
    b = j.at(descend_start + 5 * 60 - 0.001)
    assert a.beat == pytest.approx(10.0, abs=0.01)
    assert mid.beat == pytest.approx(8.0, abs=0.05)
    assert b.beat == pytest.approx(6.0, abs=0.01)
    assert mid.carrier == pytest.approx(195.0, abs=0.5)


def test_envelope_fades_in_and_out_and_is_full_in_the_middle():
    j = J.get("calm-reset")
    assert j.envelope_at(0.0) == 0.0
    assert j.envelope_at(j.seconds) == 0.0
    assert j.envelope_at(J.FADE_IN / 2) == pytest.approx(0.5, abs=0.01)
    assert j.envelope_at(j.seconds / 2) == 1.0
    assert j.envelope_at(j.seconds - J.FADE_OUT / 2) == pytest.approx(0.5, abs=0.01)


def test_locate_clamps_outside_the_journey():
    j = J.get("calm-reset")
    assert j.locate(-10.0)[0] == 0
    assert j.locate(j.seconds + 999)[0] == len(j.segments) - 1


def test_target_band_comes_from_the_frequency_being_played():
    frame = J.get("deep-delta").at(20 * 60)
    assert frame.beat == pytest.approx(2.5)
    assert frame.band == "Delta"


@pytest.mark.parametrize("journey", J.BUILTIN, ids=[j.key for j in J.BUILTIN])
def test_builtin_journeys_are_playable(journey):
    assert journey.segments, f"{journey.key} has no segments"
    assert journey.seconds >= 60
    assert journey.mode in ("binaural", "monaural", "isochronic")
    assert journey.ambience_kind in ("none", "pink", "brown")
    for seg in journey.segments:
        assert 0.1 <= seg.beat_start <= 50.0
        assert 0.1 <= seg.end_beat <= 50.0
        # Carriers must stay where binaural beating actually works, and always
        # above the beat itself (carrier - beat/2 has to remain audible).
        for carrier in (seg.carrier_start, seg.end_carrier):
            assert 40.0 <= carrier <= 1000.0
            assert carrier - seg.beat_start / 2 > 20.0
        assert 0.0 <= seg.ambience <= 1.0
    for band in journey.bands:
        assert band in BANDS


def test_segment_rejects_impossible_values():
    with pytest.raises(ValueError):
        J.Segment("bad", 0, 10.0)
    with pytest.raises(ValueError):
        J.Segment("bad", 60, -1.0)


def test_journey_survives_a_json_round_trip(tmp_path):
    original = J.get("theta-gateway")
    path = tmp_path / "j.json"
    original.save(path)
    loaded = J.Journey.load(path)
    assert loaded == original
    assert json.loads(path.read_text())["segments"][0]["name"] == original.segments[0].name


def test_user_journeys_ignore_broken_files(tmp_path):
    J.get("calm-reset").save(tmp_path / "good.json")
    (tmp_path / "bad.json").write_text("{not json")
    loaded = J.user_journeys(tmp_path)
    assert [j.key for j in loaded] == ["calm-reset"]
    assert J.user_journeys(tmp_path / "nope") == []


def test_get_rejects_unknown_keys():
    with pytest.raises(KeyError):
        J.get("no-such-journey")


def test_custom_builds_a_single_hold():
    j = J.custom(beat=7.83, minutes=5, carrier=190.0)
    assert j.seconds == 300
    assert j.at(150).beat == pytest.approx(7.83)
    assert j.bands == ("Theta",)
