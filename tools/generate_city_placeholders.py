"""Generate offline landmark illustrations for the 17 Commons photo questions."""
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "static" / "images"

art = {
    "Paris": '<path d="M600 105 445 620h310L600 105Zm0 110 90 320H510l90-320Z" fill="#303b53" fill-rule="evenodd"/><path d="M520 390h160M470 540h260M585 205h30" stroke="#f7c46a" stroke-width="18"/><path d="M600 105v-35" stroke="#303b53" stroke-width="10"/>',
    "NewYork": '<path d="M130 620V385h95v-80h95v315m30 0V335h95v285m35 0V245h90v-80h30v-55h20v55h30v80h90v375m35 0V365h85v255m35 0V295h100v325" fill="#34445d"/><path d="M585 110V75m-400 345h45m155-45h40m390 40h40" stroke="#f7d78b" stroke-width="10"/>',
    "SanFrancisco": '<path d="M160 520h880M285 520V190h45v330m540 0V190h45v330M160 390l145-180 145 180 150-180 150 180 145-180 145 180" fill="none" stroke="#c95843" stroke-width="26" stroke-linejoin="round"/><path d="M100 575h1000" stroke="#426e85" stroke-width="85"/>',
    "Rome": '<path d="M195 350q405-215 810 0v270H195Z" fill="#d9a96f"/><path d="M225 390q375-165 750 0M220 470h760M220 550h760" fill="none" stroke="#92664d" stroke-width="12"/><g fill="#835b49"><path d="M260 615v-74a36 36 0 0 1 72 0v74Zm110 0v-74a36 36 0 0 1 72 0v74Zm110 0v-74a36 36 0 0 1 72 0v74Zm110 0v-74a36 36 0 0 1 72 0v74Zm110 0v-74a36 36 0 0 1 72 0v74Zm110 0v-74a36 36 0 0 1 72 0v74Z"/></g>',
    "Venice": '<path d="M0 510h1200v240H0Z" fill="#44788d"/><path d="M110 500V265h160v235m25 0V310h140v190m345 0V280h170v220m25 0V330h130v170" fill="#d7a27c"/><path d="M90 265h200l-100-80Zm680 15h190l-95-75Z" fill="#8c5a53"/><path d="M335 500q265-300 530 0" fill="none" stroke="#e7d2aa" stroke-width="40"/><path d="M305 500h590" stroke="#e7d2aa" stroke-width="25"/><path d="M290 650q280 70 560 0" fill="none" stroke="#25364c" stroke-width="22"/>',
    "Barcelona": '<path d="M265 620V355l55-65 55 65v265m80 0V255l55-80 55 80v365m70 0V175l65-115 65 115v445m70 0V280l55-75 55 75v340" fill="#b88667"/><path d="M525 175V90m175-30V20m160 185V110" stroke="#b88667" stroke-width="20"/><path d="M235 620h735" stroke="#7a554e" stroke-width="24"/><g fill="#634d55"><circle cx="320" cy="430" r="23"/><circle cx="510" cy="350" r="27"/><circle cx="700" cy="270" r="31"/><circle cx="860" cy="360" r="27"/></g>',
    "Berlin": '<path d="M190 315h820v55H190Zm40-70h740l40 70H190Zm50 125h85v245h-85Zm135 0h85v245h-85Zm135 0h85v245h-85Zm135 0h85v245h-85Zm135 0h85v245h-85ZM170 615h860v32H170Z" fill="#d6bc90"/><path d="M495 242l45-55 60 32 60-32 45 55" fill="#435361"/><circle cx="600" cy="180" r="18" fill="#435361"/>',
    "Amsterdam": '<path d="M90 620V320h145v300m20 0V280h155v340m20 0V335h150v285m20 0V260h165v360m20 0V320h150v300" fill="#ac735e"/><path d="M90 320l72-90 73 90m20-40 77-100 78 100m20 55 75-90 75 90m20-75 82-115 83 115m20 60 75-85 75 85" fill="none" stroke="#744a4d" stroke-width="25"/><path d="M0 625h1200v125H0Z" fill="#497891"/><g fill="#f7dc9a"><path d="M145 390h32v60h-32Zm145-35h32v60h-32Zm230 40h32v60h-32Zm150-55h32v60h-32Zm225 60h32v60h-32Z"/></g>',
    "Sydney": '<path d="M120 560q135-265 280 0Q360 345 555 560q-5-285 215 0 20-225 300 0Z" fill="#f4e9d2"/><path d="M100 570h1000" stroke="#d0c7b9" stroke-width="22"/><path d="M0 625h1200v125H0Z" fill="#467d96"/><path d="M60 500h1100" stroke="#587084" stroke-width="18"/>',
    "Singapore": '<path d="M235 615V260h175v355m110 0V260h175v355m110 0V260h175v355" fill="#607e91"/><path d="M180 245q420-90 840 0v65q-420 95-840 0Z" fill="#d1b384"/><path d="M0 630h1200v120H0Z" fill="#3d788b"/><path d="M100 575h1000" stroke="#d9c7a3" stroke-width="12"/>',
    "HongKong": '<path d="M65 610V350h125v260m20 0V230h115v380m25 0V310h120v300m25 0V170h135v440m25 0V260h120v350m25 0V195h115v415m25 0V330h125v280" fill="#36546c"/><path d="M0 640h1200v110H0Z" fill="#386f87"/><path d="M80 395h95m45-125h95m235-45h100m260 20h90" stroke="#efc678" stroke-width="12"/><path d="M555 170V90" stroke="#36546c" stroke-width="9"/>',
    "Dubai": '<path d="M600 70 570 130v65l-30 65v80l-35 80v80l-45 120h280l-45-120v-80l-35-80v-80l-30-65v-65Z" fill="#718a97"/><path d="M580 130h40m-70 130h100m-125 160h150m-220 200h290" stroke="#d7e4df" stroke-width="13"/><path d="M235 620V390h140v230m450 0V355h145v265" fill="#687b87"/>',
    "Shanghai": '<path d="M600 85v495m-65 40 65-100 65 100" stroke="#9c7384" stroke-width="24" fill="none"/><circle cx="600" cy="235" r="56" fill="#bf748a"/><circle cx="600" cy="430" r="88" fill="#bf748a"/><path d="M115 620V365h165v255m40 0V290h100v330m365 0V310h150v310m40 0V390h115v230" fill="#547486"/><path d="M0 635h1200v115H0Z" fill="#457e95"/>',
    "Beijing": '<path d="M155 610h890V485H155Zm90-140h710v-85H245Z" fill="#a84743"/><path d="M95 485h1010l-95-100H190Zm120-115h770l-85-105H300Z" fill="#c69152"/><path d="M500 610V490h200v120" fill="#6c343a"/><path d="M145 385h910m-825-115h740" stroke="#e5bc69" stroke-width="12"/>',
    "Toronto": '<path d="M180 620V370h150v250m55 0V300h145v320m320 0V330h140v290" fill="#4c657e"/><path d="M620 620 650 330l-15-45h80l-15 45 30 290Z" fill="#8aa3aa"/><ellipse cx="675" cy="300" rx="110" ry="30" fill="#bdced0"/><path d="M675 270V65" stroke="#8aa3aa" stroke-width="18"/><path d="M0 645h1200v105H0Z" fill="#4b8094"/>',
    "RiodeJaneiro": '<path d="M0 595q150-175 320-20 230-330 500-20 175-175 380 20v175H0Z" fill="#3a796f"/><path d="M555 385h90v180h-90Zm-185 25 230-55 230 55v40l-230-20-230 20Z" fill="#e6d4b0"/><circle cx="600" cy="310" r="58" fill="#e6d4b0"/><path d="M545 565h110" stroke="#b49b79" stroke-width="24"/>',
    "Istanbul": '<path d="M240 620V410h720v210Z" fill="#c7ad8b"/><path d="M335 410q265-310 530 0Z" fill="#a99588"/><path d="M490 260q110-140 220 0" fill="#a99588"/><path d="M145 620V255h55v365m800 0V255h55v365" fill="#b89d83"/><path d="M172 255V105m855 150V105" stroke="#b89d83" stroke-width="15"/><path d="M125 255h95m760 0h95" stroke="#d6bd9a" stroke-width="18"/>',
}

colors = [
    ("#90bad1", "#f4d3a6"), ("#8fb2d0", "#e8c1a3"), ("#9ac7cd", "#f3cf9f"),
    ("#c1b4c4", "#f1d2a5"), ("#a7c1c7", "#f3d7aa"), ("#b0bed4", "#f1cbb3"),
]

for index, (name, landmark) in enumerate(art.items()):
    sky, horizon = colors[index % len(colors)]
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 750" role="img">
<defs><linearGradient id="sky" x2="0" y2="1"><stop stop-color="{sky}"/><stop offset="1" stop-color="{horizon}"/></linearGradient></defs>
<path fill="url(#sky)" d="M0 0h1200v750H0z"/>
<circle cx="980" cy="145" r="72" fill="#fff4ca" opacity=".72"/>
<path d="M65 200q65-45 130 0m165-75q75-40 150 0m310 95q65-35 130 0" fill="none" stroke="#fff" stroke-width="22" stroke-linecap="round" opacity=".35"/>
{landmark}
<path d="M0 680q300-30 600 0t600 0v70H0Z" fill="#263c54" opacity=".16"/>
</svg>'''
    (OUT / f"{name}.svg").write_text(svg, encoding="utf-8")

print(f"Wrote {len(art)} SVG illustrations to {OUT}")
