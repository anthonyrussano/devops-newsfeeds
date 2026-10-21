"use strict";
const search = document.getElementById("search");
const category = document.getElementById("category");
if (search && category) {
  const items = [...document.querySelectorAll(".filterable")];
  const count = document.getElementById("result-count");
  const filter = () => {
    const query = search.value.trim().toLocaleLowerCase();
    let sources = 0;
    let stories = 0;
    for (const item of items) {
      const matches = item.dataset.search.toLocaleLowerCase().includes(query) &&
        (!category.value || JSON.parse(item.dataset.tags).includes(category.value));
      item.hidden = !matches;
      if (matches) {
        if (item.classList.contains("feed-card")) sources++;
        else stories++;
      }
    }
    count.textContent = `${sources} sources and ${stories} stories shown`;
  };
  search.addEventListener("input", filter);
  category.addEventListener("change", filter);
  filter();
}
