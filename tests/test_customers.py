def test_customer_list_masks_mobile_number(client, test_customer):
    response = client.get("/customers")

    assert response.status_code == 200

    customers = response.json()
    customer = next(
        item
        for item in customers
        if item["customer_id"] == test_customer
    )

    assert customer["name"] == "Test Customer"
    assert customer["mobile_masked"] == "******0000"
    assert "mobile" not in customer