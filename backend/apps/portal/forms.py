from django import forms


class LoginForm(forms.Form):
    phone = forms.CharField(label="Phone", max_length=16)
    password = forms.CharField(label="Password", widget=forms.PasswordInput)


class SettlementAccountForm(forms.Form):
    KIND = [("momo", "Mobile money"), ("bank", "Bank account")]
    kind = forms.ChoiceField(choices=KIND)
    provider = forms.CharField(max_length=32, help_text="Network (mtn/telecel/at) or bank code")
    account_no = forms.CharField(max_length=32)
    account_name = forms.CharField(max_length=128)

    def clean(self):
        data = super().clean()
        kind, provider = data.get("kind"), (data.get("provider") or "").strip()
        if kind == "momo":
            if provider.lower() not in ("mtn", "telecel", "at"):
                self.add_error("provider", "Network must be mtn, telecel or at.")
            data["provider"] = provider.lower()
        elif kind == "bank":
            data["provider"] = provider.upper()
        return data


class RequestSettlementForm(forms.Form):
    amount = forms.DecimalField(
        max_digits=12, decimal_places=2, required=False,
        help_text="Leave blank to settle the full available balance.",
    )
