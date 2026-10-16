# Portal branding

The logo is a native SVG redraw inspired by the supplied Portal artwork: a green P-shaped portal, an open door, an ivory document, and three sage document strokes. It uses clean geometric paths and restrained gradients. The horizontal layout places the uppercase wordmark beside the symbol and fits the site's compact header.

`app/static/branding/portal-logo.svg` is the complete wordmark, used in the header and public share password prompt. `portal-symbol.svg` contains only the symbol for the browser tab. The SVG sources are authoritative; both render directly at any screen density. Text uses the same system font family as the application.

`portal-icon-192.png` and `portal-touch-180.png` are browser-rendered exports of the SVG symbol, without generative image processing or background extraction. The touch icon has a warm cream background. Retired raster logos are no longer shipped with the application.

Palette: primary green `#244d40`, ink `#22372f`, warm cream `#f8f7f3`, and pale sage `#edf2ec`. Change CSS display sizes rather than rasterising the header artwork. Keep the symbol paths and gradients consistent between both SVG sources when editing the design.

Public share pages load the logo through their own token-scoped branding route, so the password prompt can display it without granting access to the private application's static routes or report assets.
