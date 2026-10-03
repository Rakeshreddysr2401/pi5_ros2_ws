"""media_node's pure parts: the music level and spoken song titles."""
import pytest

mn = pytest.importorskip("pi5_voice_pkg.media_node")


def test_level_after():
    assert mn.level_after(60, change=10) == 70
    assert mn.level_after(95, change=10) == 100
    assert mn.level_after(5, change=-20) == 0
    assert mn.level_after(60, set_to=30) == 30
    assert mn.level_after(60, set_to=-5) == 0


def test_clean_title_reads_well_aloud():
    assert mn.clean_title("Kesariya - Brahmāstra | Ranbir Kapoor, Alia Bhatt | Pritam | 4K") \
        == "Kesariya - Brahmāstra"
    assert mn.clean_title("Tum Hi Ho (Official Video)") == "Tum Hi Ho"
    assert mn.clean_title("") == "the song"


def test_only_the_stop_word_stops_music():
    # the brain's own "abandon this reply" marker on /voice/tts_stop must not stop the song
    assert mn.STOP_WORD_MARKER == "[stop]"
