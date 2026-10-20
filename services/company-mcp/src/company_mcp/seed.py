"""Reset the demo catalog and customers. Safe to run repeatedly; orders are left untouched.

    company-mcp-seed                  # uses TABLE_NAME (and AWS_ENDPOINT_URL locally)
    company-mcp-seed --create-table   # create the table first (emulators without Terraform)
"""

import argparse
import os
from decimal import Decimal

import boto3

from .models import Customer, CustomerStatus, InventoryItem
from .repository import Repository, customer_key, product_key
from .table import create_table

PRODUCTS = [
    InventoryItem(productId="LAPTOP-PRO", name="Laptop Pro 14", unitPrice=Decimal("3000.00"), unitCost=Decimal("2200.00"), available=10, reserved=0),
    InventoryItem(productId="MONITOR-27", name="27in 4K Monitor", unitPrice=Decimal("450.00"), unitCost=Decimal("300.00"), available=25, reserved=0),
    InventoryItem(productId="DOCK-USB-C", name="USB-C Dock", unitPrice=Decimal("180.00"), unitCost=Decimal("110.00"), available=2, reserved=0),
    # Margins differ on purpose: a 30% discount stays profitable on keyboards but not on webcams.
    InventoryItem(productId="KEYBOARD-MX", name="Mechanical Keyboard", unitPrice=Decimal("120.00"), unitCost=Decimal("60.00"), available=60, reserved=0),
    InventoryItem(productId="HEADSET-PRO", name="Noise-cancelling Headset", unitPrice=Decimal("200.00"), unitCost=Decimal("150.00"), available=30, reserved=0),
    InventoryItem(productId="WEBCAM-4K", name="4K Webcam", unitPrice=Decimal("150.00"), unitCost=Decimal("120.00"), available=20, reserved=0),
]

CUSTOMERS = [
    Customer(customerId="CUST-ACME", name="Acme Retail", email="buyer@acme.example", segment="enterprise", status=CustomerStatus.ACTIVE, creditLimit=Decimal("50000.00")),
    Customer(customerId="CUST-GLOBEX", name="Globex Startup", email="ops@globex.example", segment="smb", status=CustomerStatus.ACTIVE, creditLimit=Decimal("5000.00")),
    Customer(customerId="CUST-BLOCKED", name="Initech", email="ap@initech.example", segment="smb", status=CustomerStatus.BLOCKED, creditLimit=Decimal("0.00")),
    Customer(customerId="CUST-UMBRELLA", name="Umbrella Labs", email="procurement@umbrella.example", segment="enterprise", status=CustomerStatus.ACTIVE, creditLimit=Decimal("30000.00")),
    Customer(customerId="CUST-STARK", name="Stark Industries", email="it@stark.example", segment="enterprise", status=CustomerStatus.ACTIVE, creditLimit=Decimal("100000.00")),
    Customer(customerId="CUST-HOOLI", name="Hooli", email="office@hooli.example", segment="smb", status=CustomerStatus.ACTIVE, creditLimit=Decimal("8000.00")),
]


def seed(repo: Repository) -> None:
    for product in PRODUCTS:
        pk, sk = product_key(product.productId)
        repo.put({"pk": pk, "sk": sk, **product.model_dump()})
    for customer in CUSTOMERS:
        pk, sk = customer_key(customer.customerId)
        repo.put({"pk": pk, "sk": sk, **customer.model_dump()})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--create-table", action="store_true", help="create the table first (local DynamoDB)")
    args = parser.parse_args()

    table_name = os.environ.get("TABLE_NAME", "company")
    dynamodb = boto3.resource(
        "dynamodb",
        region_name=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"),
    )
    if args.create_table and table_name not in [t.name for t in dynamodb.tables.all()]:
        create_table(dynamodb, table_name)
    seed(Repository(dynamodb.Table(table_name)))
    print(f"Seeded {len(PRODUCTS)} products and {len(CUSTOMERS)} customers into {table_name}.")


if __name__ == "__main__":
    main()
