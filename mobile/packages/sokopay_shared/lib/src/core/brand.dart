import 'package:flutter/material.dart';

/// The SokoPay wordmark (black with the orange "A"). Use [onDark] on ink/dark
/// backgrounds — the white variant keeps the orange "A".
class SokoLogo extends StatelessWidget {
  const SokoLogo({super.key, this.height = 28, this.onDark = false});
  final double height;
  final bool onDark;

  @override
  Widget build(BuildContext context) {
    return Image.asset(
      onDark ? 'assets/brand/sokopay_logo_white.png' : 'assets/brand/sokopay_logo.png',
      package: 'sokopay_shared',
      height: height,
      fit: BoxFit.contain,
      semanticLabel: 'SokoPay',
    );
  }
}
