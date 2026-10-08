import 'package:flutter/material.dart';

/// SokoPay brand theme. Mirrors the design-system tokens used across the product
/// (ink #231F20, orange #F26522). See docs/03-Design-System.md.
class SokoColors {
  static const ink = Color(0xFF231F20);
  static const inkMuted = Color(0xFF5E5859);
  static const orange = Color(0xFFF26522);
  static const orangeDark = Color(0xFFD4541A);
  static const bg = Color(0xFFF7F5F4);
  static const surface = Color(0xFFFFFFFF);
  static const success = Color(0xFF1E8E3E);
  static const danger = Color(0xFFC62828);
  static const warning = Color(0xFFB26A00);
}

ThemeData buildSokoTheme() {
  final base = ThemeData(useMaterial3: true, brightness: Brightness.light);
  return base.copyWith(
    scaffoldBackgroundColor: SokoColors.bg,
    colorScheme: base.colorScheme.copyWith(
      primary: SokoColors.orange,
      onPrimary: Colors.white,
      surface: SokoColors.surface,
      error: SokoColors.danger,
    ),
    appBarTheme: const AppBarTheme(
      backgroundColor: SokoColors.ink,
      foregroundColor: Colors.white,
      centerTitle: false,
    ),
    elevatedButtonTheme: ElevatedButtonThemeData(
      style: ElevatedButton.styleFrom(
        backgroundColor: SokoColors.orange,
        foregroundColor: Colors.white,
        minimumSize: const Size.fromHeight(52),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
        textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: Colors.white,
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(8)),
    ),
  );
}
