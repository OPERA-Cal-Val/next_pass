from pathlib import Path

VIEWER_HTML = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "next_pass"
    / "viewer_assets"
    / "index.html"
)


def test_wkb_decoder_consumes_iso_and_ewkb_extra_dimensions():
    source = VIEWER_HTML.read_text(encoding="utf-8")

    assert "rawType & 0x80000000" in source
    assert "rawType & 0x40000000" in source
    assert "rawType & 0x20000000" in source
    assert "type -= 1000; hasZ = true" in source
    assert "if (hasZ) f64()" in source
    assert "if (hasM) f64()" in source
