"""ISO 3166-1 alpha-2 to Anexo J "Tabela X" country codes.

Anexo J (Modelo 3) identifies a source or counterparty country by a code from the
official "Tabela X" in the form instructions, not by the ISO alpha-2 code an ISIN
carries. This module is the single translation seam: at_country_code maps an ISO
alpha-2 code to its Tabela X code, or None when the code is not in the table.

ISO_TO_AT is transcribed from the official Anexo J instructions (Modelo 3, Tabela X,
"Lista de Países, Territórios ou Regiões e Respetivos Códigos"). Those codes are the
ISO 3166-1 numeric codes written as the table prints them: no leading zeros (Áustria
40, Alemanha 276). That is the CodPais value the Portal's Modelo 3 XML carries. Two
Tabela X entries are free-trade zones with no ISO alpha-2, so no ISIN prefix reaches
them (980 Ilha de Queshm/Irão, 981 Labuán/Malásia); a filer who knows a security
belongs to one enters it by hand.

Following the codebase's refuse-to-guess stance, a country absent from Tabela X (the
ISO uninhabited territories such as Antarctica or Bouvet Island) is surfaced by
detect_unmapped_countries and rendered as its raw ISO code for the filer to correct by
hand, never mapped to a fabricated number.

Mirrors the _CURRENCY_SYMBOLS idiom (i18n.py): a module-level lookup with a graceful
fallback for absent keys.
"""

from __future__ import annotations

# ISO 3166-1 alpha-2 -> Anexo J Tabela X code, transcribed from the official
# Modelo 3 instructions (see the module docstring). The PT name on each row is
# the verbatim Tabela X label, kept as an audit anchor to the source.
ISO_TO_AT: dict[str, str] = {
    "AD": "20",  # Andorra
    "AE": "784",  # Emirados Árabes Unidos
    "AF": "4",  # Afeganistão
    "AG": "28",  # Antígua e Barbuda
    "AI": "660",  # Anguilla
    "AL": "8",  # Albânia
    "AM": "51",  # Arménia
    "AO": "24",  # Angola
    "AR": "32",  # Argentina
    "AS": "16",  # Samoa Americana
    "AT": "40",  # Áustria
    "AU": "36",  # Austrália
    "AW": "533",  # Aruba
    "AX": "248",  # Alanda
    "AZ": "31",  # Azerbaijão
    "BA": "70",  # Bósnia-Herzegovina
    "BB": "52",  # Barbados
    "BD": "50",  # Bangladesh
    "BE": "56",  # Bélgica
    "BF": "854",  # Burquina Faso
    "BG": "100",  # Bulgária
    "BH": "48",  # Barém
    "BI": "108",  # Burundi
    "BJ": "204",  # Benim
    "BL": "652",  # São Bartolomeu
    "BM": "60",  # Bermudas
    "BN": "96",  # Brunei
    "BO": "68",  # Bolívia
    "BQ": "535",  # Bonaire, Saint Eustatius e Saba
    "BR": "76",  # Brasil
    "BS": "44",  # Baamas
    "BT": "64",  # Butão
    "BW": "72",  # Botsuana
    "BY": "112",  # Bielorrússia
    "BZ": "84",  # Belize
    "CA": "124",  # Canadá
    "CC": "166",  # Ilhas dos Cocos
    "CD": "180",  # República Democrática do Congo
    "CF": "140",  # República Centro-Africana
    "CG": "178",  # Congo
    "CH": "756",  # Suíça
    "CI": "384",  # Costa do Marfim
    "CK": "184",  # Ilhas Cook
    "CL": "152",  # Chile
    "CM": "120",  # Camarões
    "CN": "156",  # China
    "CO": "170",  # Colômbia
    "CR": "188",  # Costa Rica
    "CU": "192",  # Cuba
    "CV": "132",  # Cabo Verde
    "CW": "531",  # Curaçau
    "CX": "162",  # Ilha do Natal
    "CY": "196",  # Chipre
    "CZ": "203",  # República Checa
    "DE": "276",  # Alemanha
    "DJ": "262",  # Jibuti
    "DK": "208",  # Dinamarca
    "DM": "212",  # Dominica
    "DO": "214",  # República Dominicana
    "DZ": "12",  # Argélia
    "EC": "218",  # Equador
    "EE": "233",  # Estónia
    "EG": "818",  # Egito
    "EH": "732",  # Sara Ocidental
    "ER": "232",  # Eritreia
    "ES": "724",  # Espanha
    "ET": "231",  # Etiópia
    "FI": "246",  # Finlândia
    "FJ": "242",  # Fiji
    "FK": "238",  # Ilhas Falkland (Malvinas)
    "FM": "583",  # Micronésia
    "FO": "234",  # Faroé
    "FR": "250",  # França
    "GA": "266",  # Gabão
    "GB": "826",  # Reino Unido
    "GD": "308",  # Granada
    "GE": "268",  # Geórgia
    "GF": "254",  # Guiana Francesa
    "GG": "831",  # Guernesey
    "GH": "288",  # Gana
    "GI": "292",  # Gibraltar
    "GL": "304",  # Gronelândia
    "GM": "270",  # Gâmbia
    "GN": "324",  # Guiné
    "GP": "312",  # Guadalupe
    "GQ": "226",  # Guiné Equatorial
    "GR": "300",  # Grécia
    "GT": "320",  # Guatemala
    "GU": "316",  # Guame
    "GW": "624",  # Guiné-Bissau
    "GY": "328",  # Guiana
    "HK": "344",  # Hong-Kong
    "HN": "340",  # Honduras
    "HR": "191",  # Croácia
    "HT": "332",  # Haiti
    "HU": "348",  # Hungria
    "ID": "360",  # Indonésia
    "IE": "372",  # Irlanda
    "IL": "376",  # Israel
    "IM": "833",  # Ilha de Man
    "IN": "356",  # Índia
    "IQ": "368",  # Iraque
    "IR": "364",  # Irão
    "IS": "352",  # Islândia
    "IT": "380",  # Itália
    "JE": "832",  # Jersey
    "JM": "388",  # Jamaica
    "JO": "400",  # Jordânia
    "JP": "392",  # Japão
    "KE": "404",  # Quénia
    "KG": "417",  # Quirguistão
    "KH": "116",  # Camboja
    "KI": "296",  # Quiribáti
    "KM": "174",  # Comores
    "KN": "659",  # São Cristóvão e Neves
    "KP": "408",  # Coreia do Norte
    "KR": "410",  # Coreia do Sul
    "KW": "414",  # Koweit
    "KY": "136",  # Ilhas Caimão
    "KZ": "398",  # Cazaquistão
    "LA": "418",  # Laos
    "LB": "422",  # Líbano
    "LC": "662",  # Santa Lúcia
    "LI": "438",  # Liechtenstein
    "LK": "144",  # Sri Lanca
    "LR": "430",  # Libéria
    "LS": "426",  # Lesoto
    "LT": "440",  # Lituânia
    "LU": "442",  # Luxemburgo
    "LV": "428",  # Letónia
    "LY": "434",  # Líbia
    "MA": "504",  # Marrocos
    "MC": "492",  # Mónaco
    "MD": "498",  # Moldávia
    "ME": "499",  # Montenegro
    "MF": "663",  # Saint-Martin (Francesa)
    "MG": "450",  # Madagáscar
    "MH": "584",  # Ilhas Marshall
    "MK": "807",  # Antiga República Jugoslava da Macedónia
    "ML": "466",  # Mali
    "MM": "104",  # Mianmar/Birmânia
    "MN": "496",  # Mongólia
    "MO": "446",  # Macau
    "MP": "580",  # Ilhas Marianas do Norte
    "MQ": "474",  # Martinica
    "MR": "478",  # Mauritânia
    "MS": "500",  # Monserrate
    "MT": "470",  # Malta
    "MU": "480",  # Maurícia
    "MV": "462",  # Maldivas
    "MW": "454",  # Maláui
    "MX": "484",  # México
    "MY": "458",  # Malásia
    "MZ": "508",  # Moçambique
    "NA": "516",  # Namíbia
    "NC": "540",  # Nova Caledónia
    "NE": "562",  # Níger
    "NF": "574",  # Ilha Norfolk
    "NG": "566",  # Nigéria
    "NI": "558",  # Nicarágua
    "NL": "528",  # Países Baixos
    "NO": "578",  # Noruega
    "NP": "524",  # Nepal
    "NR": "520",  # Nauru
    "NU": "570",  # Niuê
    "NZ": "554",  # Nova Zelândia
    "OM": "512",  # Omã
    "PA": "591",  # Panamá
    "PE": "604",  # Peru
    "PF": "258",  # Polinésia Francesa
    "PG": "598",  # Papua-Nova Guiné
    "PH": "608",  # Filipinas
    "PK": "586",  # Paquistão
    "PL": "616",  # Polónia
    "PM": "666",  # São Pedro e Miquelão
    "PN": "612",  # Ilhas Pitcairn
    "PR": "630",  # Porto Rico
    "PS": "275",  # Estado da Palestina
    "PT": "620",  # Portugal
    "PW": "585",  # Palau
    "PY": "600",  # Paraguai
    "QA": "634",  # Catar
    "RE": "638",  # Reunião
    "RO": "642",  # Roménia
    "RS": "688",  # Sérvia
    "RU": "643",  # Rússia
    "RW": "646",  # Ruanda
    "SA": "682",  # Arábia Saudita
    "SB": "90",  # Ilhas Salomão
    "SC": "690",  # Seicheles
    "SD": "729",  # Sudão
    "SE": "752",  # Suécia
    "SG": "702",  # Singapura
    "SH": "654",  # Santa Helena, Ascensão e Tristão da Cunha
    "SI": "705",  # Eslovénia
    "SJ": "744",  # Svalbard e Jan Mayen
    "SK": "703",  # Eslováquia
    "SL": "694",  # Serra Leoa
    "SM": "674",  # São Marinho
    "SN": "686",  # Senegal
    "SO": "706",  # Somália
    "SR": "740",  # Suriname
    "SS": "728",  # Sudão do Sul
    "ST": "678",  # São Tomé e Príncipe
    "SV": "222",  # Salvador
    "SX": "534",  # Sint Maarten (Holandesa)
    "SY": "760",  # Síria
    "SZ": "748",  # Suazilândia
    "TC": "796",  # Ilhas Turcas e Caicos
    "TD": "148",  # Chade
    "TG": "768",  # Togo
    "TH": "764",  # Tailândia
    "TJ": "762",  # Tajiquistão
    "TK": "772",  # Toquelau
    "TL": "626",  # Timor-Leste
    "TM": "795",  # Turquemenistão
    "TN": "788",  # Tunísia
    "TO": "776",  # Tonga
    "TR": "792",  # Turquia
    "TT": "780",  # Trindade e Tobago
    "TV": "798",  # Tuvalu
    "TW": "158",  # Taiwan
    "TZ": "834",  # Tanzânia
    "UA": "804",  # Ucrânia
    "UG": "800",  # Uganda
    "US": "840",  # Estados Unidos da América
    "UY": "858",  # Uruguai
    "UZ": "860",  # Usbequistão
    "VA": "336",  # Santa Sé / Estado da Cidade do Vaticano
    "VC": "670",  # São Vicente e Granadinas
    "VE": "862",  # Venezuela
    "VG": "92",  # Ilhas Virgens Britânicas
    "VI": "850",  # Ilhas Virgens dos Estados Unidos
    "VN": "704",  # Vietname
    "VU": "548",  # Vanuatu
    "WF": "876",  # Wallis e Futuna
    "WS": "882",  # Samoa
    "YE": "887",  # Iémen
    "YT": "175",  # Maiote
    "ZA": "710",  # África do Sul
    "ZM": "894",  # Zâmbia
    "ZW": "716",  # Zimbabué
}


def at_country_code(iso: str) -> str | None:
    """The Anexo J Tabela X code for an ISO alpha-2 country, or None if unmapped.

    Case-insensitive on the ISO input (ISIN prefixes are upper-case, but a caller may
    pass either). None tells the caller to fall back to the raw ISO code and warn, so
    a missing mapping degrades to a recognizable placeholder, never a fabricated code.
    """
    return ISO_TO_AT.get(iso.upper())
