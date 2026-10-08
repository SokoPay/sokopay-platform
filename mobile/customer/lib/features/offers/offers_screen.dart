import 'package:flutter/material.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

/// Offers tab. Partner promotions and rewards will be listed here once partners run
/// them; until then an honest empty state (no fake offers).
class OffersScreen extends StatelessWidget {
  const OffersScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Offers')),
      body: const Center(
        child: Padding(
          padding: EdgeInsets.all(32),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Icon(Icons.card_giftcard, size: 64, color: SokoColors.orange),
            SizedBox(height: 16),
            Text('No offers right now', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
            SizedBox(height: 8),
            Text('Discounts and rewards from SokoPay and our partners will show up here.',
                textAlign: TextAlign.center, style: TextStyle(color: SokoColors.inkMuted)),
          ]),
        ),
      ),
    );
  }
}
