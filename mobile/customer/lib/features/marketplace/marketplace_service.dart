import 'package:sokopay_shared/sokopay_shared.dart';

/// Insurance & loans from licensed partners:
///   GET  /marketplace/products?category=insurance|lending
///   POST /marketplace/apply        {product_code, amount?, consent}
///   GET  /marketplace/applications
///   POST /marketplace/applications/{reference}/pay-premium   (DEMI: pay from wallet)
class MarketplaceService {
  MarketplaceService(this._api);
  final ApiClient _api;

  Future<List<FinancialProduct>> products(String category) async {
    final r = await _api.get('/marketplace/products', query: {'category': category});
    return (r.data as List).map((e) => FinancialProduct.fromJson(e)).toList();
  }

  Future<Map<String, dynamic>> apply(String productCode, String? amount, bool consent) async {
    final r = await _api.post('/marketplace/apply', data: {
      'product_code': productCode,
      if (amount != null && amount.isNotEmpty) 'amount': amount,
      'consent': consent,
    });
    return Map<String, dynamic>.from(r.data);
  }

  Future<List<Application>> applications() async {
    final r = await _api.get('/marketplace/applications');
    return (r.data as List).map((e) => Application.fromJson(e)).toList();
  }

  /// Savings / investment / pension: pay in from the wallet, or ask for money back.
  Future<void> contribute(String reference, String amount, String pin) =>
      _api.post('/marketplace/applications/$reference/contribute', data: {'amount': amount, 'pin': pin});
  Future<void> withdraw(String reference, String amount, String pin) =>
      _api.post('/marketplace/applications/$reference/withdraw', data: {'amount': amount, 'pin': pin});

  Future<void> payPremium(String reference, String amount) =>
      _api.post('/marketplace/applications/$reference/pay-premium', data: {'amount': amount});
}

class FinancialProduct {
  FinancialProduct({required this.code, required this.name, required this.category,
      required this.summary, required this.provider, required this.regulator,
      this.min, this.max});
  final String code;
  final String name;
  final String category;
  final String summary;
  final String provider;
  final String regulator;
  final String? min;
  final String? max;
  factory FinancialProduct.fromJson(Map<String, dynamic> j) => FinancialProduct(
        code: j['code'],
        name: j['name'],
        category: j['category'],
        summary: j['summary'] ?? '',
        provider: j['provider'] ?? '',
        regulator: j['regulator'] ?? '',
        min: j['min'],
        max: j['max'],
      );
}

class Application {
  Application({required this.reference, required this.product, required this.provider,
      required this.status, required this.category, this.amount, this.position});
  /// Savings / investment / pension: paid in / paid out / waiting (null for others).
  final Map<String, dynamic>? position;
  bool get isSavingsLike => const {'savings', 'investment', 'pension'}.contains(category);
  final String reference;
  final String product;
  final String provider;
  final String status;
  final String category;
  final String? amount;
  factory Application.fromJson(Map<String, dynamic> j) => Application(
        reference: j['reference'],
        product: j['product'],
        provider: j['provider'],
        status: j['status'],
        category: j['category'] ?? '',
        amount: j['amount'],
        position: j['position'] == null ? null : Map<String, dynamic>.from(j['position']),
      );
}
