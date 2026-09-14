from scripts.soak_callback import soak


def test_short_callback_soak_stays_finite_and_bounded():
    result = soak(seconds=0.05, blocksize=512, voices=4, sample_rate=48_000)
    assert result["iterations"] > 0
    assert result["late_fraction"] < 0.2
    assert result["rss_growth_bytes"] < 32 * 1024 * 1024
