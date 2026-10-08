import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// Bottom sheet asking for the 6-digit PIN before a money action (step-up). Shared by all apps.
/// Returns the PIN, or null if dismissed. The PIN is checked on the server, with the
/// same lockout as sign-in; it is never stored on the phone.
Future<String?> askPinFor(BuildContext context, {required String action}) {
  return showModalBottomSheet<String>(
    context: context,
    isScrollControlled: true,
    builder: (c) => _PinSheet(action: action),
  );
}

class _PinSheet extends StatefulWidget {
  const _PinSheet({required this.action});
  final String action;
  @override
  State<_PinSheet> createState() => _PinSheetState();
}

class _PinSheetState extends State<_PinSheet> {
  final _pin = TextEditingController();

  @override
  void dispose() {
    _pin.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.fromLTRB(24, 24, 24, 24 + MediaQuery.of(context).viewInsets.bottom),
      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        const Text('Enter your PIN', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
        const SizedBox(height: 4),
        Text('To confirm: ${widget.action}', style: const TextStyle(color: Colors.black54)),
        const SizedBox(height: 16),
        TextField(
          controller: _pin,
          autofocus: true,
          obscureText: true,
          keyboardType: TextInputType.number,
          maxLength: 6,
          textAlign: TextAlign.center,
          style: const TextStyle(fontSize: 24, letterSpacing: 12),
          inputFormatters: [FilteringTextInputFormatter.digitsOnly],
          onChanged: (v) {
            if (v.length == 6) Navigator.pop(context, v);
          },
          decoration: const InputDecoration(counterText: '', hintText: '••••••'),
        ),
        const SizedBox(height: 8),
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
      ]),
    );
  }
}
