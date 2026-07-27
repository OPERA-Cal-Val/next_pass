import earthaccess
import pytest

from utils.opera_products import fetch_hls_granule_links


@pytest.mark.integration
def test_live_cmr_fallback_search():
    """
    Hit the live CMR API to ensure earthaccess.search_data still accepts
    our specific combination of arguments without throwing an error.
    """
    try:
        results = earthaccess.search_data(
            short_name=["HLSS30", "HLSL30"],
            temporal=("2023-01-01T00:00:00", "2023-01-01T23:59:59"),
            bounding_box=(-120.0, 30.0, -119.0, 31.0),
        )
        assert isinstance(results, list)
    except Exception as e:
        pytest.fail(f"Live CMR search failed! The API contract may have changed: {e}")


@pytest.mark.integration
def test_live_hls_granule_fetch():
    """
    Hit the live CMR API to ensure our fetch_hls_granule_links function
    successfully queries and extracts data links for a known collection.
    """
    known_granule_prefix = "HLS.S30.T11SLT.2023001"

    try:
        links = fetch_hls_granule_links(known_granule_prefix)
        assert isinstance(links, list)
    except Exception as e:
        pytest.fail(f"Live CMR granule fetch failed: {e}")
