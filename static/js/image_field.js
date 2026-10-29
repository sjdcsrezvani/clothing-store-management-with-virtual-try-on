/* Image picker: previews chosen files and accepts drag-and-drop, while the
   native input still does the actual upload. Markup contract:
   [data-image-field] > .image-drop > input[type=file], plus
   [data-image-picked] > [data-image-picked-grid] and [data-image-clear]. */
(function () {
  'use strict';

  function initImageFields(root) {
    var scope = root || document;
    var fields = Array.prototype.slice.call(scope.querySelectorAll('[data-image-field]'));
    if (scope.matches && scope.matches('[data-image-field]')) fields.push(scope);
    fields.forEach(function (field) {
      if (field.dataset.imageFieldBound === '1') return;
      field.dataset.imageFieldBound = '1';

      var input = field.querySelector('input[type="file"]');
      var drop = field.querySelector('.image-drop');
      var picked = field.querySelector('[data-image-picked]');
      var grid = field.querySelector('[data-image-picked-grid]');
      var clear = field.querySelector('[data-image-clear]');
      if (!input || !drop || !picked || !grid) return;

      var urls = [];

      function revoke() {
        urls.forEach(function (url) { URL.revokeObjectURL(url); });
        urls = [];
      }

      function render() {
        revoke();
        grid.innerHTML = '';
        var files = Array.prototype.slice.call(input.files || []);
        picked.hidden = files.length === 0;
        files.forEach(function (file) {
          var url = URL.createObjectURL(file);
          urls.push(url);
          var figure = document.createElement('figure');
          var image = document.createElement('img');
          image.src = url;
          image.alt = file.name;
          var caption = document.createElement('figcaption');
          caption.textContent = file.name;
          figure.appendChild(image);
          figure.appendChild(caption);
          grid.appendChild(figure);
        });
      }

      input.addEventListener('change', render);

      ['dragenter', 'dragover'].forEach(function (type) {
        drop.addEventListener(type, function (event) {
          event.preventDefault();
          field.classList.add('is-dragging');
        });
      });
      drop.addEventListener('dragleave', function (event) {
        if (!drop.contains(event.relatedTarget)) field.classList.remove('is-dragging');
      });
      drop.addEventListener('drop', function (event) {
        event.preventDefault();
        field.classList.remove('is-dragging');
        var files = Array.prototype.filter.call(
          event.dataTransfer.files || [],
          function (file) { return /^image\//.test(file.type); }
        );
        if (!files.length) return;
        if (!input.multiple) files = files.slice(0, 1);
        try {
          var transfer = new DataTransfer();
          files.forEach(function (file) { transfer.items.add(file); });
          input.files = transfer.files;
          input.dispatchEvent(new Event('change', { bubbles: true }));
        } catch (error) {
          // Old browsers keep the click path; a dropped file is not uploaded.
        }
      });

      if (clear) {
        clear.addEventListener('click', function () {
          input.value = '';
          input.dispatchEvent(new Event('change', { bubbles: true }));
        });
      }
    });
  }

  window.initImageFields = initImageFields;
  document.addEventListener('htmx:load', function (event) { initImageFields(event.target); });
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { initImageFields(); });
  } else {
    initImageFields();
  }
})();
