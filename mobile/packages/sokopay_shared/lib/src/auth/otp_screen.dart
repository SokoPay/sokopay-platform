import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';
import 'package:provider/provider.dart';

import 'auth_controller.dart';

/// Step 2 of sign-in: enter the 6-digit code sent by SMS.
class OtpScreen extends StatefulWidget {
  const OtpScreen({super.key, required this.phone});
  final String phone;
  @override
  State<OtpScreen> createState() => _OtpScreenState();
}

class _OtpScreenState extends State<OtpScreen> {
  final _controller = TextEditingController();

  Future<void> _verify(AuthController auth) async {
    final next = await auth.verifyOtp(widget.phone, _controller.text.trim());
    if (next == null || !mounted) return;
    if (next == 'needs_pin') {
      context.go('/pin-set');
    } else if (next == 'pin_login') {
      // Existing customer: the SMS code alone isn't enough (SIM-swap protection).
      context.go('/pin-login', extra: widget.phone);
    } else {
      context.go('/home');
    }
  }

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthController>();
    return Scaffold(
      appBar: AppBar(title: const Text('Verify')),
      body: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const SizedBox(height: 16),
            Text('Enter the code sent to ${widget.phone}',
                style: const TextStyle(fontSize: 16)),
            const SizedBox(height: 16),
            TextField(
              controller: _controller,
              keyboardType: TextInputType.number,
              maxLength: 6,
              textAlign: TextAlign.center,
              style: const TextStyle(fontSize: 28, letterSpacing: 8),
              inputFormatters: [FilteringTextInputFormatter.digitsOnly],
              decoration: const InputDecoration(counterText: ''),
              onChanged: (v) {
                if (v.length == 6) _verify(auth);
              },
            ),
            if (auth.error != null) ...[
              const SizedBox(height: 12),
              Text(auth.error!, style: const TextStyle(color: Colors.red)),
            ],
            const SizedBox(height: 24),
            ElevatedButton(
              onPressed: auth.loading ? null : () => _verify(auth),
              child: const Text('Verify'),
            ),
          ],
        ),
      ),
    );
  }
}
