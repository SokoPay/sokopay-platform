import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import 'auth_controller.dart';

/// Account basics shared by all three apps: Forgot PIN, Change PIN, Profile, Close account.
/// Every rule (lockouts, Ghana Card check, 24 h send cap after a reset, name lock after
/// KYC, close-account conditions) is enforced by the server; these screens explain them.

class _PinField extends StatelessWidget {
  const _PinField({required this.controller, required this.label});
  final TextEditingController controller;
  final String label;

  @override
  Widget build(BuildContext context) => TextField(
        controller: controller,
        obscureText: true,
        maxLength: 6,
        keyboardType: TextInputType.number,
        inputFormatters: [FilteringTextInputFormatter.digitsOnly],
        decoration: InputDecoration(labelText: label, counterText: ''),
      );
}

String? _pinProblem(String a, String b) {
  if (a.length != 6) return 'Your PIN must be 6 digits.';
  if (a != b) return "The two new PINs don't match.";
  return null;
}

// --- Forgot PIN ---------------------------------------------------------------------------
class ForgotPinScreen extends StatefulWidget {
  const ForgotPinScreen({super.key, this.phone});
  final String? phone;
  @override
  State<ForgotPinScreen> createState() => _ForgotPinScreenState();
}

class _ForgotPinScreenState extends State<ForgotPinScreen> {
  late final _phone = TextEditingController(text: widget.phone ?? '+233');
  final _code = TextEditingController();
  final _card = TextEditingController();
  final _pin = TextEditingController();
  final _pin2 = TextEditingController();
  bool _sent = false;
  String? _local;

  Future<void> _send(AuthController auth) async {
    if (await auth.requestPinReset(_phone.text.trim()) && mounted) setState(() => _sent = true);
  }

  Future<void> _reset(AuthController auth) async {
    final problem = _pinProblem(_pin.text, _pin2.text);
    setState(() => _local = problem);
    if (problem != null) return;
    final ok = await auth.resetPin(phone: _phone.text.trim(), code: _code.text.trim(),
        newPin: _pin.text, ghanaCard: _card.text.trim());
    if (ok && mounted) context.go('/home');
  }

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthController>();
    final error = _local ?? auth.error;
    return Scaffold(
      appBar: AppBar(title: const Text('Forgot PIN')),
      body: ListView(padding: const EdgeInsets.all(24), children: [
        TextField(controller: _phone, enabled: !_sent, keyboardType: TextInputType.phone,
            decoration: const InputDecoration(labelText: 'Your phone number')),
        const SizedBox(height: 12),
        if (!_sent)
          ElevatedButton(onPressed: auth.loading ? null : () => _send(auth), child: const Text('Text me a code'))
        else ...[
          TextField(controller: _code, keyboardType: TextInputType.number, maxLength: 6,
              inputFormatters: [FilteringTextInputFormatter.digitsOnly],
              decoration: const InputDecoration(labelText: 'Code from the SMS', counterText: '')),
          const SizedBox(height: 12),
          TextField(controller: _card, textCapitalization: TextCapitalization.characters,
              decoration: const InputDecoration(labelText: 'Ghana Card number (if you verified it)',
                  hintText: 'GHA-123456789-0')),
          const SizedBox(height: 12),
          _PinField(controller: _pin, label: 'New 6-digit PIN'),
          const SizedBox(height: 12),
          _PinField(controller: _pin2, label: 'New PIN again'),
          const SizedBox(height: 8),
          const Text('For your security, you can send up to GH₵ 500 in the 24 hours after resetting your PIN, '
              'and other phones will be signed out.', style: TextStyle(color: SokoColors.inkMuted, fontSize: 12.5)),
          const SizedBox(height: 16),
          ElevatedButton(onPressed: auth.loading ? null : () => _reset(auth), child: const Text('Reset PIN')),
        ],
        if (error != null)
          Padding(padding: const EdgeInsets.only(top: 12), child: Text(error, style: const TextStyle(color: SokoColors.danger))),
      ]),
    );
  }
}

// --- Change PIN ---------------------------------------------------------------------------
class ChangePinScreen extends StatefulWidget {
  const ChangePinScreen({super.key});
  @override
  State<ChangePinScreen> createState() => _ChangePinScreenState();
}

class _ChangePinScreenState extends State<ChangePinScreen> {
  final _current = TextEditingController();
  final _pin = TextEditingController();
  final _pin2 = TextEditingController();
  String? _local;

  Future<void> _save(AuthController auth) async {
    final problem = _pinProblem(_pin.text, _pin2.text);
    setState(() => _local = problem);
    if (problem != null) return;
    if (await auth.changePin(_current.text, _pin.text) && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('PIN changed. Other phones have been signed out.')));
      Navigator.of(context).pop();
    }
  }

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthController>();
    final error = _local ?? auth.error;
    return Scaffold(
      appBar: AppBar(title: const Text('Change PIN')),
      body: ListView(padding: const EdgeInsets.all(24), children: [
        _PinField(controller: _current, label: 'Current PIN'),
        const SizedBox(height: 12),
        _PinField(controller: _pin, label: 'New PIN'),
        const SizedBox(height: 12),
        _PinField(controller: _pin2, label: 'New PIN again'),
        const SizedBox(height: 8),
        const Text("Don't use your MoMo PIN, your birthday or a repeated number.",
            style: TextStyle(color: SokoColors.inkMuted, fontSize: 12.5)),
        if (error != null)
          Padding(padding: const EdgeInsets.only(top: 12), child: Text(error, style: const TextStyle(color: SokoColors.danger))),
        const SizedBox(height: 20),
        ElevatedButton(onPressed: auth.loading ? null : () => _save(auth), child: const Text('Change PIN')),
      ]),
    );
  }
}

// --- Profile -------------------------------------------------------------------------------
class ProfileScreen extends StatefulWidget {
  const ProfileScreen({super.key});
  @override
  State<ProfileScreen> createState() => _ProfileScreenState();
}

class _ProfileScreenState extends State<ProfileScreen> {
  final _name = TextEditingController();
  final _email = TextEditingController();
  bool _locked = false;
  bool _loaded = false;

  @override
  void initState() {
    super.initState();
    context.read<AuthController>().loadProfile().then((p) {
      if (!mounted || p == null) return;
      setState(() {
        _name.text = p['full_name'] ?? '';
        _email.text = p['email'] ?? '';
        _locked = p['name_locked'] == true;
        _loaded = true;
      });
    });
  }

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthController>();
    return Scaffold(
      appBar: AppBar(title: const Text('Profile')),
      body: !_loaded
          ? const Center(child: CircularProgressIndicator())
          : ListView(padding: const EdgeInsets.all(24), children: [
              TextField(controller: _name, enabled: !_locked, textCapitalization: TextCapitalization.words,
                  decoration: InputDecoration(
                    labelText: 'Full name',
                    helperText: _locked ? 'From your verified Ghana Card. Contact support if it\'s wrong.' : null,
                  )),
              const SizedBox(height: 12),
              TextField(controller: _email, keyboardType: TextInputType.emailAddress,
                  decoration: const InputDecoration(labelText: 'Email (optional)', helperText: 'For statements and receipts')),
              if (auth.error != null)
                Padding(padding: const EdgeInsets.only(top: 12), child: Text(auth.error!, style: const TextStyle(color: SokoColors.danger))),
              const SizedBox(height: 20),
              ElevatedButton(
                onPressed: auth.loading
                    ? null
                    : () async {
                        final ok = await auth.saveProfile(
                            fullName: _locked ? null : _name.text.trim(), email: _email.text.trim());
                        if (ok && context.mounted) {
                          ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Saved')));
                        }
                      },
                child: const Text('Save'),
              ),
            ]),
    );
  }
}

// --- Close account --------------------------------------------------------------------------
class CloseAccountScreen extends StatefulWidget {
  const CloseAccountScreen({super.key});
  @override
  State<CloseAccountScreen> createState() => _CloseAccountScreenState();
}

class _CloseAccountScreenState extends State<CloseAccountScreen> {
  static const _reasons = ['I no longer need it', 'Moving to another service', 'Privacy concerns', 'Other'];
  final _pin = TextEditingController();
  String _reason = _reasons.first;
  bool _understood = false;

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthController>();
    return Scaffold(
      appBar: AppBar(title: const Text('Close account')),
      body: ListView(padding: const EdgeInsets.all(24), children: [
        const Icon(Icons.warning_amber_rounded, color: SokoColors.danger, size: 48),
        const SizedBox(height: 12),
        const Text('Before you close your account',
            textAlign: TextAlign.center, style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
        const SizedBox(height: 12),
        const Text(
          '• Your wallet must be empty — send or withdraw any balance first.\n'
          '• Payments in progress must have finished.\n'
          '• By law we keep your transaction records for 5 years, then delete your personal details.\n'
          '• Your number stays linked to the closed account until then. To reopen, contact support.',
          style: TextStyle(height: 1.5),
        ),
        const SizedBox(height: 16),
        DropdownButtonFormField<String>(
          initialValue: _reason,
          decoration: const InputDecoration(labelText: 'Why are you leaving?'),
          items: _reasons.map((r) => DropdownMenuItem(value: r, child: Text(r))).toList(),
          onChanged: (v) => setState(() => _reason = v ?? _reason),
        ),
        const SizedBox(height: 12),
        _PinField(controller: _pin, label: 'Your PIN'),
        CheckboxListTile(
          contentPadding: EdgeInsets.zero,
          value: _understood,
          onChanged: (v) => setState(() => _understood = v ?? false),
          title: const Text('I understand this can\'t be undone in the app.'),
        ),
        if (auth.error != null)
          Padding(padding: const EdgeInsets.only(bottom: 12), child: Text(auth.error!, style: const TextStyle(color: SokoColors.danger))),
        ElevatedButton(
          style: ElevatedButton.styleFrom(backgroundColor: SokoColors.danger),
          onPressed: auth.loading || !_understood || _pin.text.length != 6
              ? null
              : () async {
                  if (await auth.closeAccount(_pin.text, _reason) && context.mounted) context.go('/');
                },
          child: const Text('Close my account'),
        ),
      ]),
    );
  }
}
