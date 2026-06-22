"""Stripe payment integration"""
import os
import stripe
from decimal import Decimal
from typing import Optional


class StripePaymentService:
    """Service for handling Stripe payments"""

    def __init__(self):
        self.api_key = os.getenv("STRIPE_SECRET_KEY", "sk_test_")
        stripe.api_key = self.api_key

    def create_payment_intent(
        self,
        amount: Decimal,
        currency: str = "usd",
        metadata: Optional[dict] = None,
    ) -> dict:
        """Create a Stripe payment intent"""
        try:
            intent = stripe.PaymentIntent.create(
                amount=int(amount * 100),  # Convert to cents
                currency=currency,
                metadata=metadata or {},
            )
            return {
                "success": True,
                "id": intent.id,
                "client_secret": intent.client_secret,
                "amount": amount,
                "currency": currency,
                "status": intent.status,
            }
        except stripe.error.StripeError as e:
            return {
                "success": False,
                "error": str(e),
            }

    def confirm_payment_intent(self, payment_intent_id: str) -> dict:
        """Confirm a payment intent"""
        try:
            intent = stripe.PaymentIntent.retrieve(payment_intent_id)
            return {
                "success": True,
                "id": intent.id,
                "status": intent.status,
                "amount": Decimal(intent.amount) / Decimal(100),
                "charges": intent.charges.data,
            }
        except stripe.error.StripeError as e:
            return {
                "success": False,
                "error": str(e),
            }

    def refund_payment(self, payment_intent_id: str) -> dict:
        """Refund a payment"""
        try:
            intent = stripe.PaymentIntent.retrieve(payment_intent_id)
            if intent.charges.data:
                charge_id = intent.charges.data[0].id
                refund = stripe.Refund.create(charge=charge_id)
                return {
                    "success": True,
                    "refund_id": refund.id,
                    "status": refund.status,
                }
            return {
                "success": False,
                "error": "No charges found to refund",
            }
        except stripe.error.StripeError as e:
            return {
                "success": False,
                "error": str(e),
            }

    def handle_webhook(self, event: dict) -> bool:
        """Handle Stripe webhook events"""
        try:
            event_type = event["type"]

            if event_type == "payment_intent.succeeded":
                payment_intent = event["data"]["object"]
                # TODO: Update payment status in database
                return True

            elif event_type == "payment_intent.payment_failed":
                payment_intent = event["data"]["object"]
                # TODO: Update payment status in database
                return True

            return False
        except Exception as e:
            print(f"Error handling webhook: {str(e)}")
            return False


# Create singleton instance
stripe_service = StripePaymentService()
