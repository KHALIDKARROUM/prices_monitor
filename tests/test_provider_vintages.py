from __future__ import annotations


def observation(price: float, available_at: str) -> dict:
    return {
        "product": "brent", "instrument": "brent_spot", "source": "EIA/FRED - DCOILBRENTEU",
        "price": price, "source_date": "2026-01-05", "collected_at": available_at,
        "available_at": available_at, "availability_basis": "provider_timestamp",
    }


def test_provider_vintages_are_append_only_and_reconstruct_the_as_of_series(empty_database):
    empty_database.insert_observation(observation(100, "2026-01-05T10:00:00+00:00"))
    empty_database.insert_observation(observation(102, "2026-01-05T12:00:00+00:00"))
    # The provider revises back to the earlier value. The main observation
    # identity already exists, but this is still a new information vintage.
    empty_database.insert_observation(observation(100, "2026-01-05T14:00:00+00:00"))

    vintages = empty_database.provider_vintages(product="brent", instrument="brent_spot")
    assert [item["value"] for item in vintages] == [100, 102, 100]
    assert [item["revision_number"] for item in reversed(vintages)] == [1, 2, 3]
    assert vintages[0]["previous_vintage_id"] == vintages[1]["id"]
    assert all(item["availability_basis"] == "provider_timestamp" for item in vintages)

    assert empty_database.point_in_time_observations("2026-01-05T10:30:00+00:00", instrument="brent_spot")["items"][0]["price"] == 100
    assert empty_database.point_in_time_observations("2026-01-05T12:30:00+00:00", instrument="brent_spot")["items"][0]["price"] == 102
    result = empty_database.point_in_time_observations("2026-01-05T14:30:00+00:00", instrument="brent_spot")
    assert result["items"][0]["price"] == 100
    assert result["items"][0]["available_at"] == "2026-01-05T14:00:00+00:00"


def test_point_in_time_read_does_not_relabel_legacy_observations_as_available(empty_database):
    with empty_database.connect() as conn:
        conn.execute(
            """INSERT INTO price_observations
            (product, instrument, price, unit, currency, source, supplier, source_date, collected_at,
             variation, variation_pct, is_unchanged, notes)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("brent", "brent_spot", 90, "USD/baril", "USD", "EIA/FRED - DCOILBRENTEU", "",
             "2025-01-05", "2025-01-06T12:00:00+00:00", None, None, 0, "Legacy import"),
        )
    result = empty_database.point_in_time_observations("2026-01-05T12:00:00+00:00", instrument="brent_spot")
    assert result["items"] == []
    assert result["vintages"] == []


def test_point_in_time_read_waits_until_the_application_captures_a_provider_value(empty_database):
    payload = observation(100, "2026-01-05T10:00:00+00:00")
    payload["collected_at"] = "2026-01-05T12:00:00+00:00"
    empty_database.insert_observation(payload)

    before_capture = empty_database.point_in_time_observations("2026-01-05T11:00:00+00:00", instrument="brent_spot")
    after_capture = empty_database.point_in_time_observations("2026-01-05T12:30:00+00:00", instrument="brent_spot")

    assert before_capture["items"] == []
    assert after_capture["items"][0]["available_at"] == "2026-01-05T10:00:00+00:00"
    assert after_capture["items"][0]["collected_at"] == "2026-01-05T12:00:00+00:00"


def test_point_in_time_read_can_reconstruct_an_index_vintage(empty_database):
    empty_database.insert_benchmark({
        "code": "bls_wpu058", "product": "bitume", "label": "Asphalt paving mixtures and blocks",
        "value": 520, "unit": "indice (déc. 1984 = 100)", "source": "U.S. BLS Public Data API - WPU058",
        "source_date": "2026-01-01", "collected_at": "2026-01-10T09:00:00+00:00",
    })

    result = empty_database.point_in_time_observations(
        "2026-01-10T10:00:00+00:00", product="bitume", instrument="asphalt_index", data_kind="index",
    )

    assert result["items"][0]["price"] == 520
    assert result["items"][0]["data_kind"] == "index"
    assert result["vintages"][0]["availability_basis"] == "collection_observed"
