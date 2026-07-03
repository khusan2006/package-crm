from django import forms

from .models import Client, Order, OrderItem, Product


class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = ["name", "company", "email", "phone", "address", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["name", "sku", "description", "unit", "price", "stock", "is_active"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class OrderForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ["client", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None and not user.can_see_all_records:
            self.fields["client"].queryset = Client.objects.filter(owner=user)


class OrderItemForm(forms.ModelForm):
    class Meta:
        model = OrderItem
        fields = ["product", "quantity", "unit_price"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(is_active=True)
        self.fields["unit_price"].required = False
        self.fields["unit_price"].widget.attrs["placeholder"] = "Product price if empty"

    def clean(self):
        cleaned = super().clean()
        # Default to the product's current price when no price is entered
        if cleaned.get("product") and not cleaned.get("unit_price"):
            cleaned["unit_price"] = cleaned["product"].price
            self.instance.unit_price = cleaned["unit_price"]
        return cleaned


OrderItemFormSet = forms.inlineformset_factory(
    Order, OrderItem, form=OrderItemForm, extra=3, can_delete=True, min_num=1,
    validate_min=True,
)


class OrderStatusForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ["status"]
