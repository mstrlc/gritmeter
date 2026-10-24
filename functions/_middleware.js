// Cloudflare Pages middleware: send www.gritmeter.app to gritmeter.app, keeping path and query.
// Pages' _redirects file can't match on hostname, so this runs in front of every request instead.
export async function onRequest({ request, next }) {
  const url = new URL(request.url);
  if (url.hostname.startsWith("www.")) {
    url.hostname = url.hostname.slice(4);
    return Response.redirect(url.toString(), 301);
  }
  return next();
}
