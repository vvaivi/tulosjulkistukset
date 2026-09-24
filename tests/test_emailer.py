from earnings_notifier.emailer import build_message


def test_build_message_contains_company_and_source() -> None:
    message = build_message(
        "sender@example.com",
        "reader@example.com",
        [
            {
                "event_id": "1",
                "company": "Test Oyj",
                "event_date": "2027-04-24",
                "event_type": "interim",
                "description": "Interim report",
                "source_url": "https://example.test/source",
            }
        ],
    )

    assert "Test Oyj" in message.get_body(preferencelist=("plain",)).get_content()
    assert message["To"] == "reader@example.com"
