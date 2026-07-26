/**
 * Iranian mobile number handling.
 *
 * Visitors type their number in whatever shape they are used to — Persian or
 * Arabic-Indic digits, with or without the country code, with spaces or dashes.
 * Everything funnels through `normalizeIranMobile` so the contact record always
 * stores one canonical E.164 value (`+989121234567`), which is the only format
 * Chatwoot's Contact model accepts.
 */

const PERSIAN_ZERO = 0x06f0;
const ARABIC_ZERO = 0x0660;

/**
 * Converts Persian (۰-۹) and Arabic-Indic (٠-٩) digits to ASCII.
 * @param {string} value
 * @returns {string}
 */
export const toLatinDigits = (value = '') =>
  String(value).replace(/[۰-۹٠-٩]/g, char => {
    const code = char.charCodeAt(0);
    const base = code >= PERSIAN_ZERO ? PERSIAN_ZERO : ARABIC_ZERO;
    return String(code - base);
  });

/**
 * Reduces any Iranian mobile input to its 10 digit national number (9XXXXXXXXX).
 * @param {string} value
 * @returns {string} the national number, or '' when the input is not one
 */
const toNationalNumber = (value = '') => {
  let digits = toLatinDigits(value).replace(/\D/g, '');

  // +98 / 0098 / 98 country code, then a leading trunk zero
  if (digits.startsWith('0098')) digits = digits.slice(4);
  else if (digits.startsWith('98')) digits = digits.slice(2);
  if (digits.startsWith('0')) digits = digits.slice(1);

  return /^9\d{9}$/.test(digits) ? digits : '';
};

/**
 * @param {string} value
 * @returns {string} E.164 number (+989121234567), or '' when invalid
 */
export const normalizeIranMobile = (value = '') => {
  const national = toNationalNumber(value);
  return national ? `+98${national}` : '';
};

/**
 * @param {string} value
 * @returns {string} local format (09121234567), or '' when invalid
 */
export const formatIranMobileLocal = (value = '') => {
  const national = toNationalNumber(value);
  return national ? `0${national}` : '';
};

/**
 * @param {string} value
 * @returns {boolean}
 */
export const isValidIranMobile = (value = '') =>
  Boolean(toNationalNumber(value));
