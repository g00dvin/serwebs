/**
 * Macros module — quick commands stored in localStorage.
 */
(function () {
  var MACROS_KEY = "serwebs_macros";

  function getMacros() {
    try {
      return JSON.parse(localStorage.getItem(MACROS_KEY)) || [];
    } catch (e) {
      return [];
    }
  }

  function saveMacros(macros) {
    localStorage.setItem(MACROS_KEY, JSON.stringify(macros));
  }

  function addMacro(name, command, sendCR) {
    if (sendCR === undefined) sendCR = true;
    var macros = getMacros();
    macros.push({ name: name, command: command, sendCR: sendCR });
    saveMacros(macros);
    return macros;
  }

  function deleteMacro(index) {
    var macros = getMacros();
    macros.splice(index, 1);
    saveMacros(macros);
    return macros;
  }

  function executeMacro(index, sendFn) {
    var macros = getMacros();
    var macro = macros[index];
    if (!macro) return;
    var data = macro.sendCR ? macro.command + "\r\n" : macro.command;
    sendFn(data);
  }

  window.SerWebsMacros = {
    getMacros: getMacros,
    saveMacros: saveMacros,
    addMacro: addMacro,
    deleteMacro: deleteMacro,
    executeMacro: executeMacro,
  };
})();
