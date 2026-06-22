function addRow() {
  const tbody = document.querySelector("#line-items tbody");
  const row = document.createElement("tr");
  row.innerHTML = `
    <td><input data-field="item" value=""></td>
    <td><input data-field="description" value=""></td>
    <td><input data-field="weight" type="number" step="0.001" value=""></td>
    <td><input data-field="quantity" type="number" step="0.01" value="1"></td>
    <td><input data-field="unit" value=""></td>
    <td><input data-field="unit_price" type="number" step="0.01" value="0.00"></td>
    <td><input data-field="amount" type="number" step="0.01" value="0.00"></td>
    <td><input data-field="tax" type="number" step="0.01" value="0.00"></td>
    <td><input data-field="tax_code" value=""></td>
    <td><input data-field="taxable" type="checkbox" checked></td>
    <td><input data-field="suggested_qbo_account" value="General business expense"></td>
    <td><button type="button" class="icon" onclick="removeRow(this)">x</button></td>
  `;
  tbody.appendChild(row);
}

function removeRow(button) {
  button.closest("tr").remove();
}

function serializeLineItems() {
  const rows = Array.from(document.querySelectorAll("#line-items tbody tr"));
  const items = rows.map((row) => {
    const read = (field) => row.querySelector(`[data-field="${field}"]`);
    return {
      item: read("item").value,
      description: read("description").value,
      weight: read("weight").value === "" ? null : Number(read("weight").value),
      quantity: Number(read("quantity").value || 0),
      unit: read("unit").value,
      unit_price: Number(read("unit_price").value || 0),
      amount: Number(read("amount").value || 0),
      tax: Number(read("tax").value || 0),
      tax_code: read("tax_code").value,
      taxable: read("taxable").checked,
      suggested_qbo_account: read("suggested_qbo_account").value,
      confidence: 1.0
    };
  }).filter((item) => item.description || item.amount);
  document.querySelector("#line_items_json").value = JSON.stringify(items);
  return true;
}

function initImageControls() {
  const container = document.getElementById("image-container");
  const img = document.getElementById("receipt-img");
  const btnZoomIn = document.getElementById("btn-zoom-in");
  const btnZoomOut = document.getElementById("btn-zoom-out");
  const btnRotate = document.getElementById("btn-rotate");
  const btnReset = document.getElementById("btn-reset");
  const btnCropItems = document.getElementById("btn-crop-items");
  const cropSelection = document.getElementById("crop-selection");
  const splitModal = document.getElementById("split-modal");
  const splitInput = document.getElementById("split-count-input");
  const splitCancel = document.getElementById("split-cancel");
  const splitSubmit = document.getElementById("split-submit");
  const manualForm = document.getElementById("manual-line-items-form");
  const layoutBoxes = Array.from(document.querySelectorAll(".layout-box"));

  if (!container || !img) return;

  let scale = 1;
  let rotate = 0;
  let translateX = 0;
  let translateY = 0;

  let isDragging = false;
  let isCropMode = false;
  let isSelectingCrop = false;
  let startX = 0;
  let startY = 0;
  let cropStartX = 0;
  let cropStartY = 0;
  let pendingCrop = null;

  function updateTransform() {
    img.style.transform = `translate(${translateX}px, ${translateY}px) scale(${scale}) rotate(${rotate}deg)`;
    updateLayoutOverlays();
  }

  function updateLayoutOverlays() {
    if (!layoutBoxes.length) return;
    const containerRect = container.getBoundingClientRect();
    const imgRect = img.getBoundingClientRect();
    layoutBoxes.forEach((box) => {
      const x = Number(box.dataset.x || 0);
      const y = Number(box.dataset.y || 0);
      const w = Number(box.dataset.w || 0);
      const h = Number(box.dataset.h || 0);
      box.style.left = `${imgRect.left - containerRect.left + imgRect.width * x}px`;
      box.style.top = `${imgRect.top - containerRect.top + imgRect.height * y}px`;
      box.style.width = `${imgRect.width * w}px`;
      box.style.height = `${imgRect.height * h}px`;
    });
  }

  function resetView() {
    scale = 1;
    rotate = 0;
    translateX = 0;
    translateY = 0;
    updateTransform();
  }

  function setCropMode(enabled) {
    isCropMode = enabled;
    isSelectingCrop = false;
    container.classList.toggle("crop-mode", enabled);
    if (btnCropItems) {
      btnCropItems.textContent = enabled ? "Drag item area" : "Select item list";
    }
    if (!enabled && cropSelection) {
      cropSelection.hidden = true;
    }
  }

  function pointInImage(clientX, clientY) {
    const rect = img.getBoundingClientRect();
    const x = Math.max(0, Math.min(rect.width, clientX - rect.left));
    const y = Math.max(0, Math.min(rect.height, clientY - rect.top));
    return { x, y, rect };
  }

  function drawCrop(start, end) {
    if (!cropSelection) return;
    const containerRect = container.getBoundingClientRect();
    const imgRect = img.getBoundingClientRect();
    const left = imgRect.left - containerRect.left + Math.min(start.x, end.x);
    const top = imgRect.top - containerRect.top + Math.min(start.y, end.y);
    const width = Math.abs(end.x - start.x);
    const height = Math.abs(end.y - start.y);
    cropSelection.style.left = `${left}px`;
    cropSelection.style.top = `${top}px`;
    cropSelection.style.width = `${width}px`;
    cropSelection.style.height = `${height}px`;
    cropSelection.hidden = false;
  }

  function openSplitModal(crop) {
    pendingCrop = crop;
    if (splitInput) {
      const suggested = Math.max(1, Math.min(12, Math.ceil(crop.height / 450)));
      splitInput.value = String(Math.max(2, suggested));
    }
    if (splitModal) {
      splitModal.hidden = false;
    }
  }

  function closeSplitModal() {
    pendingCrop = null;
    if (splitModal) {
      splitModal.hidden = true;
    }
  }

  // Zoom In
  btnZoomIn.addEventListener("click", () => {
    if (isCropMode) return;
    scale += 0.25;
    updateTransform();
  });

  // Zoom Out
  btnZoomOut.addEventListener("click", () => {
    if (isCropMode) return;
    if (scale > 0.25) {
      scale -= 0.25;
      updateTransform();
    }
  });

  // Rotate Clockwise 90 degrees
  btnRotate.addEventListener("click", () => {
    if (isCropMode) return;
    rotate = (rotate + 90) % 360;
    updateTransform();
  });

  // Reset
  btnReset.addEventListener("click", () => {
    resetView();
    setCropMode(false);
  });

  if (btnCropItems) {
    btnCropItems.addEventListener("click", () => {
      resetView();
      setCropMode(!isCropMode);
    });
  }

  // Drag to Pan
  container.addEventListener("mousedown", (e) => {
    e.preventDefault();
    if (isCropMode) {
      const point = pointInImage(e.clientX, e.clientY);
      cropStartX = point.x;
      cropStartY = point.y;
      isSelectingCrop = true;
      drawCrop({ x: cropStartX, y: cropStartY }, { x: cropStartX, y: cropStartY });
      return;
    }
    isDragging = true;
    container.style.cursor = "grabbing";
    startX = e.clientX - translateX;
    startY = e.clientY - translateY;
  });

  window.addEventListener("mousemove", (e) => {
    if (isSelectingCrop) {
      const point = pointInImage(e.clientX, e.clientY);
      drawCrop({ x: cropStartX, y: cropStartY }, point);
      return;
    }
    if (!isDragging) return;
    translateX = e.clientX - startX;
    translateY = e.clientY - startY;
    updateTransform();
  });

  window.addEventListener("mouseup", () => {
    if (isSelectingCrop) {
      const selectionRect = cropSelection.getBoundingClientRect();
      const imgRect = img.getBoundingClientRect();
      isSelectingCrop = false;
      const selectedWidth = selectionRect.width;
      const selectedHeight = selectionRect.height;
      if (selectedWidth < 20 || selectedHeight < 20) {
        if (cropSelection) cropSelection.hidden = true;
        return;
      }
      const scaleX = img.naturalWidth / imgRect.width;
      const scaleY = img.naturalHeight / imgRect.height;
      const crop = {
        x: Math.round((selectionRect.left - imgRect.left) * scaleX),
        y: Math.round((selectionRect.top - imgRect.top) * scaleY),
        width: Math.round(selectedWidth * scaleX),
        height: Math.round(selectedHeight * scaleY)
      };
      openSplitModal(crop);
      return;
    }
    if (isDragging) {
      isDragging = false;
      container.style.cursor = "grab";
    }
  });

  // Mouse Wheel to Zoom
  container.addEventListener("wheel", (e) => {
    if (isCropMode) return;
    e.preventDefault();
    const zoomFactor = 0.1;
    if (e.deltaY < 0) {
      scale += zoomFactor;
    } else {
      if (scale > 0.25) {
        scale -= zoomFactor;
      }
    }
    updateTransform();
  }, { passive: false });

  if (splitCancel) {
    splitCancel.addEventListener("click", () => {
      closeSplitModal();
      setCropMode(false);
    });
  }

  if (splitSubmit && manualForm) {
    splitSubmit.addEventListener("click", () => {
      if (!pendingCrop) return;
      const splitCount = Math.max(1, Math.min(12, Number(splitInput.value || 3)));
      document.getElementById("crop_x").value = String(Math.max(0, pendingCrop.x));
      document.getElementById("crop_y").value = String(Math.max(0, pendingCrop.y));
      document.getElementById("crop_width").value = String(Math.max(1, pendingCrop.width));
      document.getElementById("crop_height").value = String(Math.max(1, pendingCrop.height));
      document.getElementById("split_count").value = String(splitCount);
      closeSplitModal();
      setCropMode(false);
      manualForm.requestSubmit();
    });
  }

  img.addEventListener("load", updateLayoutOverlays);
  window.addEventListener("resize", updateLayoutOverlays);
  updateLayoutOverlays();
}

function initBusyForms() {
  const overlay = document.getElementById("page-busy");
  const title = document.getElementById("busy-title");
  const message = document.getElementById("busy-message");
  if (!overlay) return;

  document.querySelectorAll("form[data-busy-title]").forEach((form) => {
    form.addEventListener("submit", () => {
      const submitter = form.querySelector('button[type="submit"]');
      if (submitter) {
        submitter.disabled = true;
      }
      title.textContent = form.dataset.busyTitle || "Working";
      message.textContent = form.dataset.busyMessage || "Please wait while the app finishes this action.";
      overlay.hidden = false;
    });
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => {
    initImageControls();
    initBusyForms();
  });
} else {
  initImageControls();
  initBusyForms();
}
