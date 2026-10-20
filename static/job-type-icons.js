/* TreeKey preview — job type -> SVG symbol mapping (local, reusable).
   Symbols live in the page's hidden <svg><defs>. The visible text label always stays beside the icon,
   so an unknown type simply falls back to the leaf and nothing is lost.
   canopy = existing #tree (crown work, protected trees)
   branch = existing #leaf (pruning, hedges)
   stump  = #stump (removal / felling) — added in the refined preview; no cutting symbol existed. */
(function (g) {
  var RULES = [
    { re: /crown\s*(reduc|thin|lift)|\bcanopy\b|\bthinning\b|\blifting\b/i, id: 'tree', group: 'Crown work' },
    { re: /prun|hedge|trim|cut\s*back/i, id: 'leaf', group: 'Pruning and hedges' },
    { re: /remov|fell|dismantl|stump|dead|dangerous/i, id: 'stump', group: 'Removal' },
    { re: /protect|tpo|conservation|preservation/i, id: 'tree', group: 'Protected trees' }
  ];
  function lookup(type) {
    var t = String(type || '');
    for (var i = 0; i < RULES.length; i++) if (RULES[i].re.test(t)) return { id: RULES[i].id, group: RULES[i].group };
    return { id: 'leaf', group: 'Tree work' };
  }
  function create(type, className) {
    var ns = 'http://www.w3.org/2000/svg', info = lookup(type), id = info.id;
    if (!document.getElementById(id)) id = 'leaf';
    var svg = document.createElementNS(ns, 'svg'), use = document.createElementNS(ns, 'use');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    svg.setAttribute('class', className || 'ornament');
    use.setAttribute('href', '#' + id);
    svg.appendChild(use);
    return svg;
  }
  g.TreeKeyJobIcons = { lookup: lookup, create: create, rules: RULES };
})(window);
