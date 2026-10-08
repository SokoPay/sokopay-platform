/// SokoPay shared mobile library.
///
/// Public surface used by both the customer and agent apps. Import this single
/// barrel file rather than reaching into `src/`.
library sokopay_shared;

// Core
export 'src/core/config.dart';
export 'src/core/theme.dart';
export 'src/core/brand.dart';
export 'src/core/token_store.dart';
export 'src/core/local_prefs.dart';
export 'src/core/api_client.dart';
export 'src/core/api_errors.dart';
export 'src/core/push_service.dart';
export 'src/core/deep_links.dart';

// Auth flow (phone → OTP → PIN) + controller
export 'src/auth/auth_controller.dart';
export 'src/auth/phone_screen.dart';
export 'src/auth/otp_screen.dart';
export 'src/auth/pin_screen.dart';
export 'src/auth/account_screens.dart';

// App lock (biometric / PIN)
export 'src/security/app_lock.dart';
export 'src/security/security_screen.dart';
export 'src/security/pin_prompt.dart';

// Notification inbox (deep-link aware)
export 'src/inbox/inbox_screen.dart';

// Statements (wallet / business balance)
export 'src/statements/statement_screen.dart';

// Legal documents, help and consent text
export 'src/legal/legal.dart';
