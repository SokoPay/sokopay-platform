import 'package:flutter/material.dart';

/// Where the money comes from: the SokoPay wallet, or one of the MoMo networks.
/// Value is "wallet", "mtn", "telecel" or "at".
class PaySourcePicker extends StatelessWidget {
  const PaySourcePicker({super.key, required this.value, required this.onChanged,
      this.showWallet = true});
  final String value;
  final ValueChanged<String> onChanged;
  final bool showWallet;

  static bool isWallet(String v) => v == 'wallet';

  @override
  Widget build(BuildContext context) {
    return DropdownButtonFormField<String>(
      initialValue: value,
      decoration: const InputDecoration(labelText: 'Pay with'),
      items: [
        if (showWallet)
          const DropdownMenuItem(value: 'wallet', child: Text('SokoPay wallet')),
        const DropdownMenuItem(value: 'mtn', child: Text('MTN MoMo')),
        const DropdownMenuItem(value: 'telecel', child: Text('Telecel Cash')),
        const DropdownMenuItem(value: 'at', child: Text('AT Money')),
      ],
      onChanged: (v) => onChanged(v!),
    );
  }
}
