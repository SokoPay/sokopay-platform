import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import '../core/api_errors.dart';
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
/// Name, phone (read-only: it's the sign-in identity), email, residential address with its
/// Ghana Post GPS digital address, and identity verification. [documentsRoute] is the app's
/// "add an ID document" screen (the customer app passes '/id-documents'); without it the
/// verification section only shows the status.
class ProfileScreen extends StatefulWidget {
  const ProfileScreen({super.key, this.documentsRoute});
  final String? documentsRoute;
  @override
  State<ProfileScreen> createState() => _ProfileScreenState();
}

final _gpsPattern = RegExp(r'^[A-Z]{2}\d{3,4}\d{4}$');

/// Null when [value] is empty or a valid Ghana Post GPS address (GA-183-8164).
String? gpsProblem(String value) {
  final compact = value.toUpperCase().replaceAll(RegExp(r'[\s-]'), '');
  if (compact.isEmpty || _gpsPattern.hasMatch(compact)) return null;
  return 'Use the format GA-183-8164 (from the GhanaPostGPS app).';
}

class _ProfileScreenState extends State<ProfileScreen> {
  final _name = TextEditingController();
  final _email = TextEditingController();
  final _address = TextEditingController();
  final _gps = TextEditingController();
  Map<String, dynamic>? _profile;
  Object? _loadError;
  String? _gpsError;
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    // Load after the first frame: never start a request while the page is being built.
    WidgetsBinding.instance.addPostFrameCallback((_) => _load());
  }

  @override
  void dispose() {
    for (final c in [_name, _email, _address, _gps]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _load() async {
    setState(() => _loadError = null);
    try {
      final p = await context.read<AuthController>().fetchProfile();
      if (!mounted) return;
      _fill(p);
    } catch (e) {
      if (mounted) setState(() => _loadError = e);
    }
  }

  void _fill(Map<String, dynamic> p) {
    setState(() {
      _profile = p;
      _name.text = (p['full_name'] ?? '').toString();
      _email.text = (p['email'] ?? '').toString();
      _address.text = (p['address'] ?? '').toString();
      _gps.text = (p['gps_address'] ?? '').toString();
    });
  }

  bool get _nameLocked => _profile?['name_locked'] == true;

  Future<void> _save(AuthController auth) async {
    final problem = gpsProblem(_gps.text);
    setState(() => _gpsError = problem);
    if (problem != null) return;
    setState(() => _saving = true);
    final p = await auth.saveProfile(
      fullName: _nameLocked ? null : _name.text.trim(),
      email: _email.text.trim(),
      address: _address.text.trim(),
      gpsAddress: _gps.text.trim(),
    );
    if (!mounted) return;
    setState(() => _saving = false);
    if (p != null) {
      _fill(p);
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Profile saved')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthController>();
    final p = _profile;
    Widget body;
    if (p == null && _loadError != null) {
      body = Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            const Icon(Icons.cloud_off, size: 48, color: SokoColors.inkMuted),
            const SizedBox(height: 12),
            Text(apiErrorMessage(_loadError!, fallback: "We couldn't load your profile."),
                textAlign: TextAlign.center),
            const SizedBox(height: 16),
            OutlinedButton(onPressed: _load, child: const Text('Try again')),
          ]),
        ),
      );
    } else if (p == null) {
      body = const Center(child: CircularProgressIndicator());
    } else {
      final verification = Map<String, dynamic>.from((p['verification'] as Map?) ?? const {});
      body = RefreshIndicator(
        onRefresh: _load,
        child: ListView(padding: const EdgeInsets.all(20), children: [
          _Header(
            name: _name.text,
            phone: (p['phone'] ?? '').toString(),
            verified: verification['ghana_card_verified'] == true,
          ),
          const SizedBox(height: 20),
          const _SectionTitle('Personal details'),
          TextField(
            controller: _name,
            enabled: !_nameLocked,
            textCapitalization: TextCapitalization.words,
            decoration: InputDecoration(
              labelText: 'Full name',
              helperText: _nameLocked ? "From your verified Ghana Card. Contact support if it's wrong." : null,
            ),
          ),
          const SizedBox(height: 12),
          TextFormField(
            key: ValueKey(p['phone']),
            initialValue: (p['phone'] ?? '').toString(),
            enabled: false,
            decoration: const InputDecoration(
              labelText: 'Phone number',
              prefixIcon: Icon(Icons.phone_iphone),
              suffixIcon: Icon(Icons.lock_outline, size: 18),
              helperText: 'The number you sign in with. To change it, contact support.',
            ),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _email,
            keyboardType: TextInputType.emailAddress,
            autocorrect: false,
            decoration: const InputDecoration(
                labelText: 'Email address (optional)', helperText: 'For statements and receipts'),
          ),
          const SizedBox(height: 24),
          const _SectionTitle('Address'),
          TextField(
            controller: _address,
            textCapitalization: TextCapitalization.words,
            minLines: 1,
            maxLines: 3,
            decoration: const InputDecoration(
                labelText: 'Residential address', hintText: 'House number, street, area, town'),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _gps,
            textCapitalization: TextCapitalization.characters,
            autocorrect: false,
            inputFormatters: [
              FilteringTextInputFormatter.allow(RegExp(r'[A-Za-z0-9\- ]')),
              LengthLimitingTextInputFormatter(14),
            ],
            decoration: InputDecoration(
              labelText: 'Ghana Post GPS address',
              hintText: 'GA-183-8164',
              prefixIcon: const Icon(Icons.location_on_outlined),
              helperText: 'Find it in the GhanaPostGPS app',
              errorText: _gpsError,
            ),
          ),
          if (auth.error != null)
            Padding(
                padding: const EdgeInsets.only(top: 12),
                child: Text(auth.error!, style: const TextStyle(color: SokoColors.danger))),
          const SizedBox(height: 16),
          ElevatedButton(
            onPressed: _saving ? null : () => _save(auth),
            child: _saving
                ? const SizedBox(
                    height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                : const Text('Save'),
          ),
          const SizedBox(height: 28),
          const _SectionTitle('Identity verification'),
          _Verification(
            data: verification,
            onAdd: widget.documentsRoute == null
                ? null
                : () async {
                    await context.push(widget.documentsRoute!);
                    if (mounted) _load();
                  },
          ),
        ]),
      );
    }
    return Scaffold(appBar: AppBar(title: const Text('Profile')), body: body);
  }
}

class _SectionTitle extends StatelessWidget {
  const _SectionTitle(this.text);
  final String text;
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: Text(text, style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w700)),
      );
}

class _Header extends StatelessWidget {
  const _Header({required this.name, required this.phone, required this.verified});
  final String name;
  final String phone;
  final bool verified;

  @override
  Widget build(BuildContext context) {
    final words = name.trim().split(RegExp(r'\s+')).where((w) => w.isNotEmpty).take(2);
    final initials = words.isEmpty ? '?' : words.map((w) => w[0].toUpperCase()).join();
    return Row(children: [
      CircleAvatar(
        radius: 28,
        backgroundColor: SokoColors.orange,
        child: Text(initials, style: const TextStyle(color: Colors.white, fontSize: 20, fontWeight: FontWeight.w700)),
      ),
      const SizedBox(width: 14),
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(name.isEmpty ? 'Your profile' : name, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
          Text(phone, style: const TextStyle(color: SokoColors.inkMuted)),
          if (verified)
            const Padding(
              padding: EdgeInsets.only(top: 2),
              child: Row(children: [
                Icon(Icons.verified, size: 16, color: SokoColors.success),
                SizedBox(width: 4),
                Text('Ghana Card verified', style: TextStyle(color: SokoColors.success, fontSize: 12.5)),
              ]),
            ),
        ]),
      ),
    ]);
  }
}

class _Verification extends StatelessWidget {
  const _Verification({required this.data, this.onAdd});
  final Map<String, dynamic> data;
  final VoidCallback? onAdd;

  @override
  Widget build(BuildContext context) {
    final cardVerified = data['ghana_card_verified'] == true;
    final docs = ((data['documents'] as List?) ?? const [])
        .map((d) => Map<String, dynamic>.from(d as Map))
        .where((d) => d['type'] != 'ghana_card')
        .toList();
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Card(
        margin: EdgeInsets.zero,
        child: ListTile(
          leading: Icon(cardVerified ? Icons.verified_user : Icons.badge_outlined,
              color: cardVerified ? SokoColors.success : SokoColors.inkMuted),
          title: const Text('Ghana Card'),
          subtitle: Text(cardVerified
              ? 'Verified with NIA ${data['ghana_card_hint'] ?? ''}'
              : 'Not verified yet. Verify it to raise your wallet limits.'),
        ),
      ),
      for (final d in docs)
        Card(
          margin: const EdgeInsets.only(top: 8),
          child: ListTile(
            leading: const Icon(Icons.description_outlined),
            title: Text('${d['type_label']} ${d['number_hint'] ?? ''}'),
            subtitle: Text([
              d['status_label'],
              if (d['expiry_date'] != null) 'expires ${d['expiry_date']}',
              if (d['status'] == 'rejected' && (d['note'] ?? '').toString().isNotEmpty) d['note'],
            ].join(' · ')),
            trailing: switch (d['status']) {
              'verified' => const Icon(Icons.check_circle, color: SokoColors.success),
              'rejected' => const Icon(Icons.error_outline, color: SokoColors.danger),
              _ => const Icon(Icons.hourglass_top, color: SokoColors.inkMuted),
            },
          ),
        ),
      if (onAdd != null) ...[
        const SizedBox(height: 12),
        OutlinedButton.icon(
          onPressed: onAdd,
          icon: const Icon(Icons.add_a_photo_outlined),
          label: Text(cardVerified ? "Add passport or driver's licence" : 'Add an ID document'),
        ),
        const SizedBox(height: 6),
        const Text(
            "Ghana Card, passport or driver's licence. A Ghana Card is checked with NIA straight away; "
            'other documents are checked by our team, usually within one working day.',
            style: TextStyle(color: SokoColors.inkMuted, fontSize: 12.5)),
      ],
    ]);
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
