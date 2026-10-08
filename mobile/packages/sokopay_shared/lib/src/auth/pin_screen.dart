import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import 'auth_controller.dart';

/// Used in two modes:
///  * PinMode.set   — a new user creates their 6-digit PIN.
///  * PinMode.login — a returning user signs in with phone + PIN.
enum PinMode { set, login }

class PinScreen extends StatefulWidget {
  const PinScreen({super.key, required this.mode, this.phone});
  final PinMode mode;
  final String? phone;
  @override
  State<PinScreen> createState() => _PinScreenState();
}

class _PinScreenState extends State<PinScreen> {
  final _pin = TextEditingController();

  Future<void> _submit(AuthController auth) async {
    final pin = _pin.text.trim();
    if (pin.length != 6) return;
    bool ok;
    if (widget.mode == PinMode.set) {
      ok = await auth.setPin(pin);
    } else {
      ok = await auth.loginWithPin(widget.phone ?? auth.me?['phone'] ?? '', pin);
    }
    if (ok && mounted) context.go('/home');
  }

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthController>();
    final isSet = widget.mode == PinMode.set;
    return Scaffold(
      appBar: AppBar(title: Text(isSet ? 'Create PIN' : 'Enter PIN')),
      body: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const SizedBox(height: 16),
            Text(
              isSet
                  ? 'Create a 6-digit PIN to approve payments. Don\'t use your MoMo PIN.'
                  : 'Enter your SokoPay PIN.',
              style: const TextStyle(fontSize: 16),
            ),
            const SizedBox(height: 24),
            TextField(
              controller: _pin,
              keyboardType: TextInputType.number,
              obscureText: true,
              maxLength: 6,
              textAlign: TextAlign.center,
              style: const TextStyle(fontSize: 28, letterSpacing: 8),
              inputFormatters: [FilteringTextInputFormatter.digitsOnly],
              decoration: const InputDecoration(counterText: ''),
            ),
            if (auth.error != null) ...[
              const SizedBox(height: 12),
              Text(auth.error!, style: const TextStyle(color: Colors.red)),
            ],
            const SizedBox(height: 24),
            ElevatedButton(
              onPressed: auth.loading ? null : () => _submit(auth),
              child: Text(isSet ? 'Set PIN' : 'Sign in'),
            ),
            if (!isSet)
              TextButton(
                onPressed: () => context.push('/forgot-pin', extra: widget.phone ?? auth.me?['phone']),
                child: const Text('Forgot PIN?'),
              ),
          ],
        ),
      ),
    );
  }
}
