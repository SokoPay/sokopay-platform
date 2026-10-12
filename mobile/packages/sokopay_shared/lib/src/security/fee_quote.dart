import '../core/api_client.dart';

/// What SokoPay will charge for a payment, from GET /fees/quote, so the customer sees it
/// before confirming with their PIN. If the quote can't be fetched, [text] is empty and the
/// server still charges (and shows on the receipt) the fee in force.
class FeeQuote {
  FeeQuote({required this.fee, required this.feeMinor, required this.total, this.promotion = ''});

  final String fee;
  final int feeMinor;
  final String total;
  final String promotion;

  /// " + GH₵ 1.00 fee" (or " (no fee: Christmas promo)"), for the PIN prompt.
  String get text {
    if (feeMinor > 0) return ' + $fee fee${promotion.isNotEmpty ? ' ($promotion)' : ''}';
    return promotion.isNotEmpty ? ' (no fee: $promotion)' : '';
  }

  static Future<FeeQuote?> fetch(ApiClient api, String product, String amount) async {
    try {
      final r = await api.get('/fees/quote', query: {'product': product, 'amount': amount});
      final d = Map<String, dynamic>.from(r.data as Map);
      return FeeQuote(fee: d['fee'] ?? '', feeMinor: (d['fee_minor'] as num?)?.toInt() ?? 0,
          total: d['total'] ?? '', promotion: d['promotion'] ?? '');
    } catch (_) {
      return null;
    }
  }
}
