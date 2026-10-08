import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

import '../business/merchant_service.dart';
import 'pin_confirm.dart';

/// Add where settlements are paid. Mobile money always; bank only when the payment
/// partner supports bank payouts (otherwise the option is shown disabled, with why).
/// New accounts are name-checked by SokoPay before they can receive money, and the
/// business owner is notified when someone else adds one.
class AddAccountScreen extends StatefulWidget {
  const AddAccountScreen({super.key, required this.bankAvailable});
  final bool bankAvailable;
  @override
  State<AddAccountScreen> createState() => _AddAccountScreenState();
}

class _AddAccountScreenState extends State<AddAccountScreen> {
  final _form = GlobalKey<FormState>();
  final _number = TextEditingController();
  final _name = TextEditingController();
  final _bankCode = TextEditingController();
  String _kind = 'momo';
  String _network = 'mtn';
  bool _busy = false;

  @override
  void dispose() {
    _number.dispose();
    _name.dispose();
    _bankCode.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (!_form.currentState!.validate()) return;
    final pin = await askForPin(context, action: 'add a settlement account');
    if (pin == null || !mounted) return;
    setState(() => _busy = true);
    try {
      await MerchantService(context.read<ApiClient>()).addAccount(
        kind: _kind,
        provider: _kind == 'momo' ? _network : _bankCode.text.trim(),
        accountNo: _number.text.trim(),
        accountName: _name.text.trim(),
        pin: pin,
      );
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
          content: Text('Account added. SokoPay will verify the name before paying into it.')));
      Navigator.of(context).pop();
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(apiErrorMessage(e))));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final isMomo = _kind == 'momo';
    return Scaffold(
      appBar: AppBar(title: const Text('Add settlement account')),
      body: Form(
        key: _form,
        child: ListView(padding: const EdgeInsets.all(16), children: [
          SegmentedButton<String>(
            segments: [
              const ButtonSegment(value: 'momo', label: Text('Mobile money'), icon: Icon(Icons.phone_android)),
              ButtonSegment(
                value: 'bank',
                label: const Text('Bank'),
                icon: const Icon(Icons.account_balance),
                enabled: widget.bankAvailable,
              ),
            ],
            selected: {_kind},
            onSelectionChanged: (s) => setState(() => _kind = s.first),
          ),
          if (!widget.bankAvailable)
            const Padding(
              padding: EdgeInsets.only(top: 8),
              child: Text('Bank settlements are coming soon — our payment partner is enabling them.',
                  style: TextStyle(color: SokoColors.inkMuted, fontSize: 12)),
            ),
          const SizedBox(height: 16),
          if (isMomo)
            DropdownButtonFormField<String>(
              initialValue: _network,
              decoration: const InputDecoration(labelText: 'Network'),
              items: const [
                DropdownMenuItem(value: 'mtn', child: Text('MTN MoMo')),
                DropdownMenuItem(value: 'telecel', child: Text('Telecel Cash')),
                DropdownMenuItem(value: 'at', child: Text('AT Money')),
              ],
              onChanged: (v) => setState(() => _network = v ?? 'mtn'),
            )
          else
            TextFormField(
              controller: _bankCode,
              textCapitalization: TextCapitalization.characters,
              decoration: const InputDecoration(labelText: 'Bank code', hintText: 'e.g. GCB'),
              validator: (v) => (v ?? '').trim().isEmpty ? 'Enter the bank code' : null,
            ),
          const SizedBox(height: 12),
          TextFormField(
            controller: _number,
            keyboardType: isMomo ? TextInputType.phone : TextInputType.number,
            decoration: InputDecoration(
              labelText: isMomo ? 'Mobile money number' : 'Account number',
              hintText: isMomo ? '0241234567' : null,
            ),
            validator: (v) {
              final digits = (v ?? '').replaceAll(RegExp(r'\D'), '');
              if (isMomo) {
                final ok = (digits.length == 10 && digits.startsWith('0')) ||
                    (digits.length == 12 && digits.startsWith('233'));
                return ok ? null : 'Enter a Ghana number, e.g. 0241234567';
              }
              return digits.length >= 6 && digits.length <= 20 ? null : 'Enter a 6–20 digit account number';
            },
          ),
          const SizedBox(height: 12),
          TextFormField(
            controller: _name,
            textCapitalization: TextCapitalization.words,
            decoration: const InputDecoration(
              labelText: 'Name on the account',
              helperText: 'Must match the registered name — SokoPay checks it.',
            ),
            validator: (v) => (v ?? '').trim().length < 2 ? 'Enter the account name' : null,
          ),
          const SizedBox(height: 24),
          ElevatedButton(
            onPressed: _busy ? null : _save,
            child: _busy
                ? const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                : const Text('Add account'),
          ),
        ]),
      ),
    );
  }
}
