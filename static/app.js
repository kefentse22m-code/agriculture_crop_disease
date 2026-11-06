const APPLICATION_PREFIX = window.location.pathname.includes("/cropdetaction") ? "/cropdetaction" : "";

const fallbackCropRegions = {
  Maize: ["KwaZulu-Natal", "Free State", "Mpumalanga", "North West"],
  Wheat: ["Western Cape", "Free State", "Northern Cape"],
  Soybean: ["Mpumalanga", "KwaZulu-Natal", "Limpopo"],
  Grapes: ["Western Cape", "Northern Cape"],
  Apples: ["Western Cape", "Eastern Cape"],
  Citrus: ["Limpopo", "Mpumalanga", "Eastern Cape"],
};

const cropSelect = document.getElementById("crop");
const regionSelect = document.getElementById("region");
const predictionForm = document.getElementById("prediction-form");
const resultContent = document.getElementById("result-content");
const emptyState = document.getElementById("empty-state");
const analysisButton = document.getElementById("analysis-button");
const aiResult = document.getElementById("ai-result");
const toast = document.getElementById("toast");

function showToast(message) {
  if (!toast) return;
  toast.textContent = message;
  toast.classList.add("show");
  window.clearTimeout(showToast.timeoutId);
  showToast.timeoutId = window.setTimeout(() => {
    toast.classList.remove("show");
  }, 2600);
}

function setResultVisible(isVisible) {
  if (emptyState) emptyState.hidden = isVisible;
  if (resultContent) resultContent.hidden = !isVisible;
}

function populateCropOptions(options) {
  if (!cropSelect) return;

  const entries = options && options.length ? options : Object.keys(fallbackCropRegions);
  cropSelect.innerHTML = '<option value="">Select a crop</option>' + entries.map((crop) => `<option value="${crop}">${crop}</option>`).join("");
}

function populateRegionOptions(regionList) {
  if (!regionSelect) return;
  regionSelect.disabled = !regionList || !regionList.length;
  regionSelect.innerHTML = '<option value="">Choose a region</option>' + regionList.map((region) => `<option value="${region}">${region}</option>`).join("");
}

async function loadCrops() {
  try {
    const response = await fetch(`${APPLICATION_PREFIX}/api/options`);
    if (!response.ok) throw new Error("Unavailable");
    const payload = await response.json();
    const crops = payload.crops || Object.keys(fallbackCropRegions);
    populateCropOptions(crops);
  } catch (error) {
    populateCropOptions(Object.keys(fallbackCropRegions));
  }
}

async function loadRegionsForCrop(selectedCrop) {
  if (!selectedCrop) {
    populateRegionOptions([]);
    return;
  }

  try {
    const response = await fetch(`${APPLICATION_PREFIX}/api/options?crop=${encodeURIComponent(selectedCrop)}`);
    if (!response.ok) throw new Error("Unavailable");

    const payload = await response.json();
    populateRegionOptions(payload.regions || []);
    return;
  } catch (error) {
    const regions = fallbackCropRegions[selectedCrop] || [];
    populateRegionOptions(regions);
  }
}

function bindCropChange() {
  if (!cropSelect) return;

  cropSelect.addEventListener("change", () => {
    loadRegionsForCrop(cropSelect.value);
  });
}

function formatDisplayDate(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-ZA", { day: "numeric", month: "short", year: "numeric" }).format(date);
}

function renderResult(payload) {
  if (!payload) return;
  setResultVisible(true);

  const harvest = payload.harvest_window || {};
  const modelWindow = payload.model_window || null;

  const harvestDates = document.getElementById("harvest-dates");
  const resultSummary = document.getElementById("result-summary");
  const resultCrop = document.getElementById("result-crop");
  const resultRegion = document.getElementById("result-region");
  const resultPlanted = document.getElementById("result-planted");
  const calendarCode = document.getElementById("calendar-code");
  const modelBox = document.getElementById("model-box");
  const resultNote = document.getElementById("result-note");

  if (harvestDates) harvestDates.textContent = `${formatDisplayDate(harvest.start)} — ${formatDisplayDate(harvest.end)}`;
  if (resultSummary) resultSummary.textContent = `${payload.crop} • ${payload.region}`;
  if (resultCrop) resultCrop.textContent = payload.crop;
  if (resultRegion) resultRegion.textContent = payload.region;
  if (resultPlanted) resultPlanted.textContent = payload.planting_date_label || payload.planting_date || "--";
  if (calendarCode) calendarCode.textContent = payload.calendar_code || "N/A";

  if (modelWindow) {
    modelBox.innerHTML = `Experimental model estimate: ${formatDisplayDate(modelWindow.start)} — ${formatDisplayDate(modelWindow.end)}`;
  } else {
    modelBox.textContent = "No trained model estimate is available in this environment.";
  }

  if (resultNote) resultNote.textContent = payload.note || "Regional estimate only.";
}

async function submitPrediction(event) {
  event.preventDefault();
  if (!cropSelect || !regionSelect || !document.getElementById("planting-date")) return;

  const crop = cropSelect.value;
  const region = regionSelect.value;
  const plantingDate = document.getElementById("planting-date").value;

  if (!crop || !region || !plantingDate) {
    showToast("Please choose crop, region and planting date.");
    return;
  }

  try {
    const response = await fetch(`${APPLICATION_PREFIX}/api/predict`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ crop, region, planting_date: plantingDate }),
    });

    if (!response.ok) {
      throw new Error((await response.json()).error || "Prediction request failed.");
    }

    const payload = await response.json();
    renderResult(payload);
    showToast("Harvest window estimated.");
  } catch (error) {
    const fallbackStart = new Date(plantingDate);
    fallbackStart.setDate(fallbackStart.getDate() + 70);
    const fallbackEnd = new Date(plantingDate);
    fallbackEnd.setDate(fallbackEnd.getDate() + 120);

    renderResult({
      crop,
      region,
      planting_date: plantingDate,
      planting_date_label: formatDisplayDate(plantingDate),
      calendar_code: "REGIONAL",
      harvest_window: {
        start: fallbackStart.toISOString().split("T")[0],
        end: fallbackEnd.toISOString().split("T")[0],
      },
      model_window: null,
      note: "This is a local fallback estimate because the server prediction endpoint is not available yet.",
    });
    showToast("Fallback estimate generated.");
  }
}

async function requestAnalysis() {
  const harvestDates = document.getElementById("harvest-dates");
  const resultCrop = document.getElementById("result-crop");
  const resultRegion = document.getElementById("result-region");
  const resultPlanted = document.getElementById("result-planted");
  const calendarCode = document.getElementById("calendar-code");
  const analysisText = document.getElementById("analysis-text");

  if (!harvestDates || !resultCrop || !resultRegion || !resultPlanted || !calendarCode || !analysisText) return;

  const placeholder = {
    crop: resultCrop.textContent,
    region: resultRegion.textContent,
    planting_date_label: resultPlanted.textContent,
    calendar_code: calendarCode.textContent,
    harvest_window: { start_label: harvestDates.textContent.split("—")[0].trim(), end_label: harvestDates.textContent.split("—")[1].trim() },
  };

  try {
    const response = await fetch(`${APPLICATION_PREFIX}/api/analysis`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(placeholder),
    });

    if (!response.ok) {
      throw new Error("AI analysis unavailable.");
    }

    const payload = await response.json();
    analysisText.textContent = payload.analysis || "No AI analysis returned.";
    aiResult.hidden = false;
  } catch (error) {
    analysisText.textContent = "AI guidance is unavailable right now. Please confirm the crop, region and planting window in the field before making decisions.";
    aiResult.hidden = false;
  }
}

if (cropSelect) {
  bindCropChange();
  loadCrops();
}

if (predictionForm) {
  predictionForm.addEventListener("submit", submitPrediction);
}

if (analysisButton) {
  analysisButton.addEventListener("click", requestAnalysis);
}
