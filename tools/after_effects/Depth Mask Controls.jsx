/* Depth Mask Controls 1.0.0
 * Select one grayscale depth layer, then File > Scripts > Run Script File.
 * Depth: transition midpoint, 0 near to 100 far.
 * Range: transition width, 0 hard to 100 gradual. Invert swaps near/far.
 * Use the resulting layer as a Luma Track Matte. Person alpha goes AFTER this
 * remap. Controls are relative to the export's fixed range, not meters.
 * Native AE effects only; no plug-in, generated maps or project settings change.
 */
(function (g) {
    var SIGN = '// LDR_DEPTH_MASK_v1\n';
    var NAMES = ['Depth', 'Range', 'Invert', 'Depth Mask - Remap'];
    var MATCH = ['ADBE Slider Control', 'ADBE Slider Control', 'ADBE Checkbox Control', 'ADBE Pro Levels2'];
    var COMMON = SIGN + 'var d = clamp(effect("Depth")(1), 0, 100) / 100;\n' +
        'var r = Math.max(clamp(effect("Range")(1), 0, 100) / 100, 1/65535);\n';
    var EXPRESSIONS = {
        'ADBE Pro Levels2-0004': COMMON + 'd - r/2;',
        'ADBE Pro Levels2-0005': COMMON + 'd + r/2;',
        'ADBE Pro Levels2-0007': SIGN + 'effect("Invert")(1) >= 0.5 ? 1 : 0;',
        'ADBE Pro Levels2-0008': SIGN + 'effect("Invert")(1) >= 0.5 ? 0 : 1;'
    };
    function preflight(layer) {
        if (!(layer instanceof AVLayer) || layer.nullLayer || !layer.hasVideo || layer.locked)
            throw Error('Select one unlocked grayscale depth image or precomp layer.');
        var fx = layer.property('ADBE Effect Parade'), found = [], i, j, f, count = 0;
        for (i = 0; i < NAMES.length; i++) {
            found[i] = null;
            for (j = 1; j <= fx.numProperties; j++) {
                f = fx.property(j);
                if (f.name === NAMES[i]) {
                    if (found[i]) throw Error('Duplicate control name: ' + NAMES[i]);
                    found[i] = f; count++;
                    if (f.matchName !== MATCH[i]) throw Error('Existing effect uses the name ' + NAMES[i] + '. Rename it first.');
                }
            }
            if (!fx.canAddProperty(MATCH[i])) throw Error('Native effect unavailable: ' + MATCH[i]);
        }
        if (count && count !== NAMES.length) throw Error('A partial Depth Mask stack or conflicting control exists. No changes made.');
        if (count) {
            for (var key in EXPRESSIONS) if (EXPRESSIONS.hasOwnProperty(key))
                if (found[3].property(key).expression !== EXPRESSIONS[key])
                    throw Error('The existing Depth Mask remap was edited. No changes made.');
            return {existing: true};
        }
        return {existing: false};
    }
    function apply(layer, defaults) {
        var state = preflight(layer);
        if (state.existing) return {created: false, layerId: layer.id};
        defaults = defaults || {};
        var values = [defaults.depth === undefined ? 50 : defaults.depth,
                      defaults.range === undefined ? 100 : defaults.range,
                      defaults.invert ? 1 : 0];
        for (var v = 0; v < 2; v++) if (typeof values[v] !== 'number' || !isFinite(values[v])) throw Error('Controls need finite numbers.');
        var fx = layer.property('ADBE Effect Parade'), originalCount = fx.numProperties;
        try {
            // addProperty invalidates earlier effect references; always reacquire.
            for (var i = 0; i < NAMES.length; i++) {
                var added = fx.addProperty(MATCH[i]); added.name = NAMES[i];
                if (i < 3) added.property(1).setValue(values[i]);
            }
            var remap = fx.property(NAMES[3]);
            remap.property('ADBE Pro Levels2-0006').setValue(1);
            remap.property('ADBE Pro Levels2-0038').setValue(2);
            remap.property('ADBE Pro Levels2-0039').setValue(2);
            for (var key in EXPRESSIONS) if (EXPRESSIONS.hasOwnProperty(key)) {
                var prop = remap.property(key);
                if (!prop.canSetExpression) throw Error('Effect cannot use expressions: ' + key);
                prop.expression = EXPRESSIONS[key];
                prop.valueAtTime(layer.containingComp.time, false);
                if (prop.expressionError) throw Error(prop.expressionError);
            }
            return {created: true, layerId: layer.id};
        } catch (error) {
            while (fx.numProperties > originalCount) fx.property(fx.numProperties).remove();
            throw error;
        }
    }
    g.LDRDepthMask = {version: '1.0.0', apply: apply, preflight: preflight, names: NAMES};
    if (g.LDR_DEPTH_MASK_LIBRARY_ONLY) return;
    var undo = false;
    try {
        if (!app.project || !(app.project.activeItem instanceof CompItem) || app.project.activeItem.selectedLayers.length !== 1)
            throw Error('Select one depth-map layer in your composition, then run this script again.');
        var target = app.project.activeItem.selectedLayers[0];
        preflight(target);
        app.beginUndoGroup('Add Depth Mask Controls'); undo = true;
        apply(target);
    } catch (error) { alert('Depth Mask Controls\n\n' + String(error)); }
    finally { if (undo) app.endUndoGroup(); }
})($.global);
