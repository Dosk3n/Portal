# Portal branding

The main logo is the supplied Portal artwork recoloured to the application's forest green, sage, and warm cream palette. The complete artwork is preserved in `app/static/branding/portal-logo.png` and used in the responsive header and public share password prompt.

`app/static/branding/portal-mark.png` is a transparent, symbol-only derivative for the browser tab and touch icon. It was prepared with the built-in image editor using the supplied artwork as its edit target. The final edit request preserved the P/door/document geometry and palette, removed the wordmark and floor reflection, and required a clean transparent background with safe padding.

Palette: primary green `#244d40`, ink `#22372f`, warm cream `#f8f7f3`, and pale sage `#edf2ec`. Do not recolour or regenerate the complete logo when only changing its display size.

Public share pages load the logo through their own token-scoped branding route, so the password prompt can display it without granting access to the private application's static routes or report assets.
