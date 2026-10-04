/**
 * Deliberately narrow, dependency-free encoder for the pinned CLAP RoBERTa
 * tokenizer.json. The caller must verify the asset's cryptographic pin before
 * calling createTokenizer; this module validates the supported configuration.
 * No vocabulary, merge data, normalization, network access, or padding is built in.
 */
export const MAX_LENGTH = 77;
export const MAX_INPUT_CODE_UNITS = 4096;

// Pinned tokenizers 0.22.2 Unicode letter/number classes, verified against its
// ByteLevel pre-tokenizer for every Unicode scalar. Host \p{L}/\p{N} tables
// drift across browser/Node Unicode versions. These character ranges are not
// vocabulary/model data. Check with tokenizer-reference.py --all-unicode.
// Full scalar class-map SHA-256: f8f427506a2760101bde8049cdb38b50ad92853242a0935c587ec3724ddc8621
const LETTERS = [
  String.raw`\u{41}-\u{5A}\u{61}-\u{7A}\u{AA}\u{B5}\u{BA}\u{C0}-\u{D6}\u{D8}-\u{F6}\u{F8}-\u{2C1}\u{2C6}-\u{2D1}`,
  String.raw`\u{2E0}-\u{2E4}\u{2EC}\u{2EE}\u{370}-\u{374}\u{376}-\u{377}\u{37A}-\u{37D}\u{37F}\u{386}\u{388}-\u{38A}\u{38C}`,
  String.raw`\u{38E}-\u{3A1}\u{3A3}-\u{3F5}\u{3F7}-\u{481}\u{48A}-\u{52F}\u{531}-\u{556}\u{559}\u{560}-\u{588}`,
  String.raw`\u{5D0}-\u{5EA}\u{5EF}-\u{5F2}\u{620}-\u{64A}\u{66E}-\u{66F}\u{671}-\u{6D3}\u{6D5}\u{6E5}-\u{6E6}`,
  String.raw`\u{6EE}-\u{6EF}\u{6FA}-\u{6FC}\u{6FF}\u{710}\u{712}-\u{72F}\u{74D}-\u{7A5}\u{7B1}\u{7CA}-\u{7EA}`,
  String.raw`\u{7F4}-\u{7F5}\u{7FA}\u{800}-\u{815}\u{81A}\u{824}\u{828}\u{840}-\u{858}\u{860}-\u{86A}\u{870}-\u{887}`,
  String.raw`\u{889}-\u{88E}\u{8A0}-\u{8C9}\u{904}-\u{939}\u{93D}\u{950}\u{958}-\u{961}\u{971}-\u{980}\u{985}-\u{98C}`,
  String.raw`\u{98F}-\u{990}\u{993}-\u{9A8}\u{9AA}-\u{9B0}\u{9B2}\u{9B6}-\u{9B9}\u{9BD}\u{9CE}\u{9DC}-\u{9DD}`,
  String.raw`\u{9DF}-\u{9E1}\u{9F0}-\u{9F1}\u{9FC}\u{A05}-\u{A0A}\u{A0F}-\u{A10}\u{A13}-\u{A28}\u{A2A}-\u{A30}`,
  String.raw`\u{A32}-\u{A33}\u{A35}-\u{A36}\u{A38}-\u{A39}\u{A59}-\u{A5C}\u{A5E}\u{A72}-\u{A74}\u{A85}-\u{A8D}`,
  String.raw`\u{A8F}-\u{A91}\u{A93}-\u{AA8}\u{AAA}-\u{AB0}\u{AB2}-\u{AB3}\u{AB5}-\u{AB9}\u{ABD}\u{AD0}\u{AE0}-\u{AE1}`,
  String.raw`\u{AF9}\u{B05}-\u{B0C}\u{B0F}-\u{B10}\u{B13}-\u{B28}\u{B2A}-\u{B30}\u{B32}-\u{B33}\u{B35}-\u{B39}\u{B3D}`,
  String.raw`\u{B5C}-\u{B5D}\u{B5F}-\u{B61}\u{B71}\u{B83}\u{B85}-\u{B8A}\u{B8E}-\u{B90}\u{B92}-\u{B95}\u{B99}-\u{B9A}`,
  String.raw`\u{B9C}\u{B9E}-\u{B9F}\u{BA3}-\u{BA4}\u{BA8}-\u{BAA}\u{BAE}-\u{BB9}\u{BD0}\u{C05}-\u{C0C}\u{C0E}-\u{C10}`,
  String.raw`\u{C12}-\u{C28}\u{C2A}-\u{C39}\u{C3D}\u{C58}-\u{C5A}\u{C5D}\u{C60}-\u{C61}\u{C80}\u{C85}-\u{C8C}`,
  String.raw`\u{C8E}-\u{C90}\u{C92}-\u{CA8}\u{CAA}-\u{CB3}\u{CB5}-\u{CB9}\u{CBD}\u{CDD}-\u{CDE}\u{CE0}-\u{CE1}`,
  String.raw`\u{CF1}-\u{CF2}\u{D04}-\u{D0C}\u{D0E}-\u{D10}\u{D12}-\u{D3A}\u{D3D}\u{D4E}\u{D54}-\u{D56}\u{D5F}-\u{D61}`,
  String.raw`\u{D7A}-\u{D7F}\u{D85}-\u{D96}\u{D9A}-\u{DB1}\u{DB3}-\u{DBB}\u{DBD}\u{DC0}-\u{DC6}\u{E01}-\u{E30}`,
  String.raw`\u{E32}-\u{E33}\u{E40}-\u{E46}\u{E81}-\u{E82}\u{E84}\u{E86}-\u{E8A}\u{E8C}-\u{EA3}\u{EA5}\u{EA7}-\u{EB0}`,
  String.raw`\u{EB2}-\u{EB3}\u{EBD}\u{EC0}-\u{EC4}\u{EC6}\u{EDC}-\u{EDF}\u{F00}\u{F40}-\u{F47}\u{F49}-\u{F6C}`,
  String.raw`\u{F88}-\u{F8C}\u{1000}-\u{102A}\u{103F}\u{1050}-\u{1055}\u{105A}-\u{105D}\u{1061}\u{1065}-\u{1066}`,
  String.raw`\u{106E}-\u{1070}\u{1075}-\u{1081}\u{108E}\u{10A0}-\u{10C5}\u{10C7}\u{10CD}\u{10D0}-\u{10FA}\u{10FC}-\u{1248}`,
  String.raw`\u{124A}-\u{124D}\u{1250}-\u{1256}\u{1258}\u{125A}-\u{125D}\u{1260}-\u{1288}\u{128A}-\u{128D}\u{1290}-\u{12B0}`,
  String.raw`\u{12B2}-\u{12B5}\u{12B8}-\u{12BE}\u{12C0}\u{12C2}-\u{12C5}\u{12C8}-\u{12D6}\u{12D8}-\u{1310}\u{1312}-\u{1315}`,
  String.raw`\u{1318}-\u{135A}\u{1380}-\u{138F}\u{13A0}-\u{13F5}\u{13F8}-\u{13FD}\u{1401}-\u{166C}\u{166F}-\u{167F}`,
  String.raw`\u{1681}-\u{169A}\u{16A0}-\u{16EA}\u{16F1}-\u{16F8}\u{1700}-\u{1711}\u{171F}-\u{1731}\u{1740}-\u{1751}`,
  String.raw`\u{1760}-\u{176C}\u{176E}-\u{1770}\u{1780}-\u{17B3}\u{17D7}\u{17DC}\u{1820}-\u{1878}\u{1880}-\u{1884}`,
  String.raw`\u{1887}-\u{18A8}\u{18AA}\u{18B0}-\u{18F5}\u{1900}-\u{191E}\u{1950}-\u{196D}\u{1970}-\u{1974}\u{1980}-\u{19AB}`,
  String.raw`\u{19B0}-\u{19C9}\u{1A00}-\u{1A16}\u{1A20}-\u{1A54}\u{1AA7}\u{1B05}-\u{1B33}\u{1B45}-\u{1B4C}\u{1B83}-\u{1BA0}`,
  String.raw`\u{1BAE}-\u{1BAF}\u{1BBA}-\u{1BE5}\u{1C00}-\u{1C23}\u{1C4D}-\u{1C4F}\u{1C5A}-\u{1C7D}\u{1C80}-\u{1C8A}`,
  String.raw`\u{1C90}-\u{1CBA}\u{1CBD}-\u{1CBF}\u{1CE9}-\u{1CEC}\u{1CEE}-\u{1CF3}\u{1CF5}-\u{1CF6}\u{1CFA}\u{1D00}-\u{1DBF}`,
  String.raw`\u{1E00}-\u{1F15}\u{1F18}-\u{1F1D}\u{1F20}-\u{1F45}\u{1F48}-\u{1F4D}\u{1F50}-\u{1F57}\u{1F59}\u{1F5B}\u{1F5D}`,
  String.raw`\u{1F5F}-\u{1F7D}\u{1F80}-\u{1FB4}\u{1FB6}-\u{1FBC}\u{1FBE}\u{1FC2}-\u{1FC4}\u{1FC6}-\u{1FCC}\u{1FD0}-\u{1FD3}`,
  String.raw`\u{1FD6}-\u{1FDB}\u{1FE0}-\u{1FEC}\u{1FF2}-\u{1FF4}\u{1FF6}-\u{1FFC}\u{2071}\u{207F}\u{2090}-\u{209C}\u{2102}`,
  String.raw`\u{2107}\u{210A}-\u{2113}\u{2115}\u{2119}-\u{211D}\u{2124}\u{2126}\u{2128}\u{212A}-\u{212D}\u{212F}-\u{2139}`,
  String.raw`\u{213C}-\u{213F}\u{2145}-\u{2149}\u{214E}\u{2183}-\u{2184}\u{2C00}-\u{2CE4}\u{2CEB}-\u{2CEE}\u{2CF2}-\u{2CF3}`,
  String.raw`\u{2D00}-\u{2D25}\u{2D27}\u{2D2D}\u{2D30}-\u{2D67}\u{2D6F}\u{2D80}-\u{2D96}\u{2DA0}-\u{2DA6}\u{2DA8}-\u{2DAE}`,
  String.raw`\u{2DB0}-\u{2DB6}\u{2DB8}-\u{2DBE}\u{2DC0}-\u{2DC6}\u{2DC8}-\u{2DCE}\u{2DD0}-\u{2DD6}\u{2DD8}-\u{2DDE}\u{2E2F}`,
  String.raw`\u{3005}-\u{3006}\u{3031}-\u{3035}\u{303B}-\u{303C}\u{3041}-\u{3096}\u{309D}-\u{309F}\u{30A1}-\u{30FA}`,
  String.raw`\u{30FC}-\u{30FF}\u{3105}-\u{312F}\u{3131}-\u{318E}\u{31A0}-\u{31BF}\u{31F0}-\u{31FF}\u{3400}-\u{4DBF}`,
  String.raw`\u{4E00}-\u{A48C}\u{A4D0}-\u{A4FD}\u{A500}-\u{A60C}\u{A610}-\u{A61F}\u{A62A}-\u{A62B}\u{A640}-\u{A66E}`,
  String.raw`\u{A67F}-\u{A69D}\u{A6A0}-\u{A6E5}\u{A717}-\u{A71F}\u{A722}-\u{A788}\u{A78B}-\u{A7CD}\u{A7D0}-\u{A7D1}\u{A7D3}`,
  String.raw`\u{A7D5}-\u{A7DC}\u{A7F2}-\u{A801}\u{A803}-\u{A805}\u{A807}-\u{A80A}\u{A80C}-\u{A822}\u{A840}-\u{A873}`,
  String.raw`\u{A882}-\u{A8B3}\u{A8F2}-\u{A8F7}\u{A8FB}\u{A8FD}-\u{A8FE}\u{A90A}-\u{A925}\u{A930}-\u{A946}\u{A960}-\u{A97C}`,
  String.raw`\u{A984}-\u{A9B2}\u{A9CF}\u{A9E0}-\u{A9E4}\u{A9E6}-\u{A9EF}\u{A9FA}-\u{A9FE}\u{AA00}-\u{AA28}\u{AA40}-\u{AA42}`,
  String.raw`\u{AA44}-\u{AA4B}\u{AA60}-\u{AA76}\u{AA7A}\u{AA7E}-\u{AAAF}\u{AAB1}\u{AAB5}-\u{AAB6}\u{AAB9}-\u{AABD}\u{AAC0}`,
  String.raw`\u{AAC2}\u{AADB}-\u{AADD}\u{AAE0}-\u{AAEA}\u{AAF2}-\u{AAF4}\u{AB01}-\u{AB06}\u{AB09}-\u{AB0E}\u{AB11}-\u{AB16}`,
  String.raw`\u{AB20}-\u{AB26}\u{AB28}-\u{AB2E}\u{AB30}-\u{AB5A}\u{AB5C}-\u{AB69}\u{AB70}-\u{ABE2}\u{AC00}-\u{D7A3}`,
  String.raw`\u{D7B0}-\u{D7C6}\u{D7CB}-\u{D7FB}\u{F900}-\u{FA6D}\u{FA70}-\u{FAD9}\u{FB00}-\u{FB06}\u{FB13}-\u{FB17}\u{FB1D}`,
  String.raw`\u{FB1F}-\u{FB28}\u{FB2A}-\u{FB36}\u{FB38}-\u{FB3C}\u{FB3E}\u{FB40}-\u{FB41}\u{FB43}-\u{FB44}\u{FB46}-\u{FBB1}`,
  String.raw`\u{FBD3}-\u{FD3D}\u{FD50}-\u{FD8F}\u{FD92}-\u{FDC7}\u{FDF0}-\u{FDFB}\u{FE70}-\u{FE74}\u{FE76}-\u{FEFC}`,
  String.raw`\u{FF21}-\u{FF3A}\u{FF41}-\u{FF5A}\u{FF66}-\u{FFBE}\u{FFC2}-\u{FFC7}\u{FFCA}-\u{FFCF}\u{FFD2}-\u{FFD7}`,
  String.raw`\u{FFDA}-\u{FFDC}\u{10000}-\u{1000B}\u{1000D}-\u{10026}\u{10028}-\u{1003A}\u{1003C}-\u{1003D}`,
  String.raw`\u{1003F}-\u{1004D}\u{10050}-\u{1005D}\u{10080}-\u{100FA}\u{10280}-\u{1029C}\u{102A0}-\u{102D0}`,
  String.raw`\u{10300}-\u{1031F}\u{1032D}-\u{10340}\u{10342}-\u{10349}\u{10350}-\u{10375}\u{10380}-\u{1039D}`,
  String.raw`\u{103A0}-\u{103C3}\u{103C8}-\u{103CF}\u{10400}-\u{1049D}\u{104B0}-\u{104D3}\u{104D8}-\u{104FB}`,
  String.raw`\u{10500}-\u{10527}\u{10530}-\u{10563}\u{10570}-\u{1057A}\u{1057C}-\u{1058A}\u{1058C}-\u{10592}`,
  String.raw`\u{10594}-\u{10595}\u{10597}-\u{105A1}\u{105A3}-\u{105B1}\u{105B3}-\u{105B9}\u{105BB}-\u{105BC}`,
  String.raw`\u{105C0}-\u{105F3}\u{10600}-\u{10736}\u{10740}-\u{10755}\u{10760}-\u{10767}\u{10780}-\u{10785}`,
  String.raw`\u{10787}-\u{107B0}\u{107B2}-\u{107BA}\u{10800}-\u{10805}\u{10808}\u{1080A}-\u{10835}\u{10837}-\u{10838}`,
  String.raw`\u{1083C}\u{1083F}-\u{10855}\u{10860}-\u{10876}\u{10880}-\u{1089E}\u{108E0}-\u{108F2}\u{108F4}-\u{108F5}`,
  String.raw`\u{10900}-\u{10915}\u{10920}-\u{10939}\u{10980}-\u{109B7}\u{109BE}-\u{109BF}\u{10A00}\u{10A10}-\u{10A13}`,
  String.raw`\u{10A15}-\u{10A17}\u{10A19}-\u{10A35}\u{10A60}-\u{10A7C}\u{10A80}-\u{10A9C}\u{10AC0}-\u{10AC7}`,
  String.raw`\u{10AC9}-\u{10AE4}\u{10B00}-\u{10B35}\u{10B40}-\u{10B55}\u{10B60}-\u{10B72}\u{10B80}-\u{10B91}`,
  String.raw`\u{10C00}-\u{10C48}\u{10C80}-\u{10CB2}\u{10CC0}-\u{10CF2}\u{10D00}-\u{10D23}\u{10D4A}-\u{10D65}`,
  String.raw`\u{10D6F}-\u{10D85}\u{10E80}-\u{10EA9}\u{10EB0}-\u{10EB1}\u{10EC2}-\u{10EC4}\u{10F00}-\u{10F1C}\u{10F27}`,
  String.raw`\u{10F30}-\u{10F45}\u{10F70}-\u{10F81}\u{10FB0}-\u{10FC4}\u{10FE0}-\u{10FF6}\u{11003}-\u{11037}`,
  String.raw`\u{11071}-\u{11072}\u{11075}\u{11083}-\u{110AF}\u{110D0}-\u{110E8}\u{11103}-\u{11126}\u{11144}\u{11147}`,
  String.raw`\u{11150}-\u{11172}\u{11176}\u{11183}-\u{111B2}\u{111C1}-\u{111C4}\u{111DA}\u{111DC}\u{11200}-\u{11211}`,
  String.raw`\u{11213}-\u{1122B}\u{1123F}-\u{11240}\u{11280}-\u{11286}\u{11288}\u{1128A}-\u{1128D}\u{1128F}-\u{1129D}`,
  String.raw`\u{1129F}-\u{112A8}\u{112B0}-\u{112DE}\u{11305}-\u{1130C}\u{1130F}-\u{11310}\u{11313}-\u{11328}`,
  String.raw`\u{1132A}-\u{11330}\u{11332}-\u{11333}\u{11335}-\u{11339}\u{1133D}\u{11350}\u{1135D}-\u{11361}`,
  String.raw`\u{11380}-\u{11389}\u{1138B}\u{1138E}\u{11390}-\u{113B5}\u{113B7}\u{113D1}\u{113D3}\u{11400}-\u{11434}`,
  String.raw`\u{11447}-\u{1144A}\u{1145F}-\u{11461}\u{11480}-\u{114AF}\u{114C4}-\u{114C5}\u{114C7}\u{11580}-\u{115AE}`,
  String.raw`\u{115D8}-\u{115DB}\u{11600}-\u{1162F}\u{11644}\u{11680}-\u{116AA}\u{116B8}\u{11700}-\u{1171A}`,
  String.raw`\u{11740}-\u{11746}\u{11800}-\u{1182B}\u{118A0}-\u{118DF}\u{118FF}-\u{11906}\u{11909}\u{1190C}-\u{11913}`,
  String.raw`\u{11915}-\u{11916}\u{11918}-\u{1192F}\u{1193F}\u{11941}\u{119A0}-\u{119A7}\u{119AA}-\u{119D0}\u{119E1}`,
  String.raw`\u{119E3}\u{11A00}\u{11A0B}-\u{11A32}\u{11A3A}\u{11A50}\u{11A5C}-\u{11A89}\u{11A9D}\u{11AB0}-\u{11AF8}`,
  String.raw`\u{11BC0}-\u{11BE0}\u{11C00}-\u{11C08}\u{11C0A}-\u{11C2E}\u{11C40}\u{11C72}-\u{11C8F}\u{11D00}-\u{11D06}`,
  String.raw`\u{11D08}-\u{11D09}\u{11D0B}-\u{11D30}\u{11D46}\u{11D60}-\u{11D65}\u{11D67}-\u{11D68}\u{11D6A}-\u{11D89}`,
  String.raw`\u{11D98}\u{11EE0}-\u{11EF2}\u{11F02}\u{11F04}-\u{11F10}\u{11F12}-\u{11F33}\u{11FB0}\u{12000}-\u{12399}`,
  String.raw`\u{12480}-\u{12543}\u{12F90}-\u{12FF0}\u{13000}-\u{1342F}\u{13441}-\u{13446}\u{13460}-\u{143FA}`,
  String.raw`\u{14400}-\u{14646}\u{16100}-\u{1611D}\u{16800}-\u{16A38}\u{16A40}-\u{16A5E}\u{16A70}-\u{16ABE}`,
  String.raw`\u{16AD0}-\u{16AED}\u{16B00}-\u{16B2F}\u{16B40}-\u{16B43}\u{16B63}-\u{16B77}\u{16B7D}-\u{16B8F}`,
  String.raw`\u{16D40}-\u{16D6C}\u{16E40}-\u{16E7F}\u{16F00}-\u{16F4A}\u{16F50}\u{16F93}-\u{16F9F}\u{16FE0}-\u{16FE1}`,
  String.raw`\u{16FE3}\u{17000}-\u{187F7}\u{18800}-\u{18CD5}\u{18CFF}-\u{18D08}\u{1AFF0}-\u{1AFF3}\u{1AFF5}-\u{1AFFB}`,
  String.raw`\u{1AFFD}-\u{1AFFE}\u{1B000}-\u{1B122}\u{1B132}\u{1B150}-\u{1B152}\u{1B155}\u{1B164}-\u{1B167}`,
  String.raw`\u{1B170}-\u{1B2FB}\u{1BC00}-\u{1BC6A}\u{1BC70}-\u{1BC7C}\u{1BC80}-\u{1BC88}\u{1BC90}-\u{1BC99}`,
  String.raw`\u{1D400}-\u{1D454}\u{1D456}-\u{1D49C}\u{1D49E}-\u{1D49F}\u{1D4A2}\u{1D4A5}-\u{1D4A6}\u{1D4A9}-\u{1D4AC}`,
  String.raw`\u{1D4AE}-\u{1D4B9}\u{1D4BB}\u{1D4BD}-\u{1D4C3}\u{1D4C5}-\u{1D505}\u{1D507}-\u{1D50A}\u{1D50D}-\u{1D514}`,
  String.raw`\u{1D516}-\u{1D51C}\u{1D51E}-\u{1D539}\u{1D53B}-\u{1D53E}\u{1D540}-\u{1D544}\u{1D546}\u{1D54A}-\u{1D550}`,
  String.raw`\u{1D552}-\u{1D6A5}\u{1D6A8}-\u{1D6C0}\u{1D6C2}-\u{1D6DA}\u{1D6DC}-\u{1D6FA}\u{1D6FC}-\u{1D714}`,
  String.raw`\u{1D716}-\u{1D734}\u{1D736}-\u{1D74E}\u{1D750}-\u{1D76E}\u{1D770}-\u{1D788}\u{1D78A}-\u{1D7A8}`,
  String.raw`\u{1D7AA}-\u{1D7C2}\u{1D7C4}-\u{1D7CB}\u{1DF00}-\u{1DF1E}\u{1DF25}-\u{1DF2A}\u{1E030}-\u{1E06D}`,
  String.raw`\u{1E100}-\u{1E12C}\u{1E137}-\u{1E13D}\u{1E14E}\u{1E290}-\u{1E2AD}\u{1E2C0}-\u{1E2EB}\u{1E4D0}-\u{1E4EB}`,
  String.raw`\u{1E5D0}-\u{1E5ED}\u{1E5F0}\u{1E7E0}-\u{1E7E6}\u{1E7E8}-\u{1E7EB}\u{1E7ED}-\u{1E7EE}\u{1E7F0}-\u{1E7FE}`,
  String.raw`\u{1E800}-\u{1E8C4}\u{1E900}-\u{1E943}\u{1E94B}\u{1EE00}-\u{1EE03}\u{1EE05}-\u{1EE1F}\u{1EE21}-\u{1EE22}`,
  String.raw`\u{1EE24}\u{1EE27}\u{1EE29}-\u{1EE32}\u{1EE34}-\u{1EE37}\u{1EE39}\u{1EE3B}\u{1EE42}\u{1EE47}\u{1EE49}\u{1EE4B}`,
  String.raw`\u{1EE4D}-\u{1EE4F}\u{1EE51}-\u{1EE52}\u{1EE54}\u{1EE57}\u{1EE59}\u{1EE5B}\u{1EE5D}\u{1EE5F}`,
  String.raw`\u{1EE61}-\u{1EE62}\u{1EE64}\u{1EE67}-\u{1EE6A}\u{1EE6C}-\u{1EE72}\u{1EE74}-\u{1EE77}\u{1EE79}-\u{1EE7C}`,
  String.raw`\u{1EE7E}\u{1EE80}-\u{1EE89}\u{1EE8B}-\u{1EE9B}\u{1EEA1}-\u{1EEA3}\u{1EEA5}-\u{1EEA9}\u{1EEAB}-\u{1EEBB}`,
  String.raw`\u{20000}-\u{2A6DF}\u{2A700}-\u{2B739}\u{2B740}-\u{2B81D}\u{2B820}-\u{2CEA1}\u{2CEB0}-\u{2EBE0}`,
  String.raw`\u{2EBF0}-\u{2EE5D}\u{2F800}-\u{2FA1D}\u{30000}-\u{3134A}\u{31350}-\u{323AF}`,
].join('');
const NUMBERS = [
  String.raw`\u{30}-\u{39}\u{B2}-\u{B3}\u{B9}\u{BC}-\u{BE}\u{660}-\u{669}\u{6F0}-\u{6F9}\u{7C0}-\u{7C9}\u{966}-\u{96F}`,
  String.raw`\u{9E6}-\u{9EF}\u{9F4}-\u{9F9}\u{A66}-\u{A6F}\u{AE6}-\u{AEF}\u{B66}-\u{B6F}\u{B72}-\u{B77}\u{BE6}-\u{BF2}`,
  String.raw`\u{C66}-\u{C6F}\u{C78}-\u{C7E}\u{CE6}-\u{CEF}\u{D58}-\u{D5E}\u{D66}-\u{D78}\u{DE6}-\u{DEF}\u{E50}-\u{E59}`,
  String.raw`\u{ED0}-\u{ED9}\u{F20}-\u{F33}\u{1040}-\u{1049}\u{1090}-\u{1099}\u{1369}-\u{137C}\u{16EE}-\u{16F0}`,
  String.raw`\u{17E0}-\u{17E9}\u{17F0}-\u{17F9}\u{1810}-\u{1819}\u{1946}-\u{194F}\u{19D0}-\u{19DA}\u{1A80}-\u{1A89}`,
  String.raw`\u{1A90}-\u{1A99}\u{1B50}-\u{1B59}\u{1BB0}-\u{1BB9}\u{1C40}-\u{1C49}\u{1C50}-\u{1C59}\u{2070}\u{2074}-\u{2079}`,
  String.raw`\u{2080}-\u{2089}\u{2150}-\u{2182}\u{2185}-\u{2189}\u{2460}-\u{249B}\u{24EA}-\u{24FF}\u{2776}-\u{2793}\u{2CFD}`,
  String.raw`\u{3007}\u{3021}-\u{3029}\u{3038}-\u{303A}\u{3192}-\u{3195}\u{3220}-\u{3229}\u{3248}-\u{324F}\u{3251}-\u{325F}`,
  String.raw`\u{3280}-\u{3289}\u{32B1}-\u{32BF}\u{A620}-\u{A629}\u{A6E6}-\u{A6EF}\u{A830}-\u{A835}\u{A8D0}-\u{A8D9}`,
  String.raw`\u{A900}-\u{A909}\u{A9D0}-\u{A9D9}\u{A9F0}-\u{A9F9}\u{AA50}-\u{AA59}\u{ABF0}-\u{ABF9}\u{FF10}-\u{FF19}`,
  String.raw`\u{10107}-\u{10133}\u{10140}-\u{10178}\u{1018A}-\u{1018B}\u{102E1}-\u{102FB}\u{10320}-\u{10323}\u{10341}`,
  String.raw`\u{1034A}\u{103D1}-\u{103D5}\u{104A0}-\u{104A9}\u{10858}-\u{1085F}\u{10879}-\u{1087F}\u{108A7}-\u{108AF}`,
  String.raw`\u{108FB}-\u{108FF}\u{10916}-\u{1091B}\u{109BC}-\u{109BD}\u{109C0}-\u{109CF}\u{109D2}-\u{109FF}`,
  String.raw`\u{10A40}-\u{10A48}\u{10A7D}-\u{10A7E}\u{10A9D}-\u{10A9F}\u{10AEB}-\u{10AEF}\u{10B58}-\u{10B5F}`,
  String.raw`\u{10B78}-\u{10B7F}\u{10BA9}-\u{10BAF}\u{10CFA}-\u{10CFF}\u{10D30}-\u{10D39}\u{10D40}-\u{10D49}`,
  String.raw`\u{10E60}-\u{10E7E}\u{10F1D}-\u{10F26}\u{10F51}-\u{10F54}\u{10FC5}-\u{10FCB}\u{11052}-\u{1106F}`,
  String.raw`\u{110F0}-\u{110F9}\u{11136}-\u{1113F}\u{111D0}-\u{111D9}\u{111E1}-\u{111F4}\u{112F0}-\u{112F9}`,
  String.raw`\u{11450}-\u{11459}\u{114D0}-\u{114D9}\u{11650}-\u{11659}\u{116C0}-\u{116C9}\u{116D0}-\u{116E3}`,
  String.raw`\u{11730}-\u{1173B}\u{118E0}-\u{118F2}\u{11950}-\u{11959}\u{11BF0}-\u{11BF9}\u{11C50}-\u{11C6C}`,
  String.raw`\u{11D50}-\u{11D59}\u{11DA0}-\u{11DA9}\u{11F50}-\u{11F59}\u{11FC0}-\u{11FD4}\u{12400}-\u{1246E}`,
  String.raw`\u{16130}-\u{16139}\u{16A60}-\u{16A69}\u{16AC0}-\u{16AC9}\u{16B50}-\u{16B59}\u{16B5B}-\u{16B61}`,
  String.raw`\u{16D70}-\u{16D79}\u{16E80}-\u{16E96}\u{1CCF0}-\u{1CCF9}\u{1D2C0}-\u{1D2D3}\u{1D2E0}-\u{1D2F3}`,
  String.raw`\u{1D360}-\u{1D378}\u{1D7CE}-\u{1D7FF}\u{1E140}-\u{1E149}\u{1E2F0}-\u{1E2F9}\u{1E4F0}-\u{1E4F9}`,
  String.raw`\u{1E5F1}-\u{1E5FA}\u{1E8C7}-\u{1E8CF}\u{1E950}-\u{1E959}\u{1EC71}-\u{1ECAB}\u{1ECAD}-\u{1ECAF}`,
  String.raw`\u{1ECB1}-\u{1ECB4}\u{1ED01}-\u{1ED2D}\u{1ED2F}-\u{1ED3D}\u{1F100}-\u{1F10C}\u{1FBF0}-\u{1FBF9}`,
].join('');
// Unicode White_Space, unlike JavaScript \s, includes NEL and excludes BOM.
const WHITESPACE = String.raw`\u0009-\u000d\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000`;
// The GPT-2/RoBERTa pattern preserves case, including its contractions.
const PIECES = new RegExp(
  "'s|'t|'re|'ve|'m|'ll|'d"
    + `| ?[${LETTERS}]+| ?[${NUMBERS}]+| ?[^${WHITESPACE}${LETTERS}${NUMBERS}]+`
    + `|[${WHITESPACE}]+(?![^${WHITESPACE}])|[${WHITESPACE}]+`, 'gu',
);
const SPECIALS = /<s>|<pad>|<\/s>|<unk>|<mask>/gu;
const TRAILING_SPACE = new RegExp(`[${WHITESPACE}]+$`, 'u');
const EXPECTED_ADDED = [
  [0, '<s>', false], [1, '<pad>', false], [2, '</s>', false],
  [3, '<unk>', false], [50264, '<mask>', true],
].map(([id, content, lstrip]) => ({
  id, content, single_word: false, lstrip, rstrip: false,
  normalized: false, special: true,
}));

function unsupported(detail) {
  throw new Error(`Unsupported CLAP tokenizer: ${detail}`);
}

// Object order is irrelevant, but unknown configuration fields fail closed.
function sameJson(actual, expected) {
  if (actual === expected) return true;
  if (!actual || !expected || typeof actual !== 'object' || typeof expected !== 'object') return false;
  if (Array.isArray(actual) !== Array.isArray(expected)) return false;
  const keys = Object.keys(expected);
  return Object.keys(actual).length === keys.length
    && keys.every(key => Object.hasOwn(actual, key) && sameJson(actual[key], expected[key]));
}

function assertConfig(value, expected, name) {
  if (!sameJson(value, expected)) unsupported(name);
}

// GPT-2's reversible byte-to-Unicode alphabet. Each byte maps to one BMP code
// point; these are symbols for byte-level BPE, not the original Unicode text.
function byteAlphabet() {
  const alphabet = new Array(256);
  let extra = 256;
  for (let byte = 0; byte < 256; byte += 1) {
    const printable = (byte >= 33 && byte <= 126)
      || (byte >= 161 && byte <= 172) || (byte >= 174 && byte <= 255);
    alphabet[byte] = String.fromCharCode(printable ? byte : extra++);
  }
  return alphabet;
}

/**
 * @param {object} tokenizerJson Parsed, already hash-verified pinned tokenizer.json.
 * @returns {(text: string) => {inputIds: number[], attentionMask: number[], truncated: boolean}}
 */
export function createTokenizer(tokenizerJson) {
  if (!tokenizerJson || typeof tokenizerJson !== 'object' || Array.isArray(tokenizerJson)) unsupported('expected JSON object');
  const expectedKeys = ['version', 'truncation', 'padding', 'added_tokens', 'normalizer', 'pre_tokenizer', 'post_processor', 'decoder', 'model'];
  assertConfig(Object.keys(tokenizerJson).sort(), expectedKeys.sort(), 'top-level fields');
  assertConfig(tokenizerJson.version, '1.0', 'version');
  assertConfig(tokenizerJson.truncation, null, 'serialized truncation');
  assertConfig(tokenizerJson.padding, {
    strategy: 'BatchLongest', direction: 'Right', pad_to_multiple_of: null,
    pad_id: 1, pad_type_id: 0, pad_token: '<pad>',
  }, 'padding configuration');
  assertConfig(tokenizerJson.normalizer, null, 'normalizer');
  assertConfig(tokenizerJson.pre_tokenizer, {
    type: 'ByteLevel', add_prefix_space: false, trim_offsets: true, use_regex: true,
  }, 'ByteLevel pre-tokenizer');
  assertConfig(tokenizerJson.post_processor, {
    type: 'RobertaProcessing', sep: ['</s>', 2], cls: ['<s>', 0],
    trim_offsets: true, add_prefix_space: false,
  }, 'RoBERTa post-processor');
  assertConfig(tokenizerJson.decoder, {
    type: 'ByteLevel', add_prefix_space: true, trim_offsets: true, use_regex: true,
  }, 'ByteLevel decoder');
  assertConfig(tokenizerJson.added_tokens, EXPECTED_ADDED, 'added/special tokens');
  const model = tokenizerJson.model;
  if (!model || typeof model !== 'object' || Array.isArray(model)) unsupported('BPE model');
  const { vocab, merges, ...modelConfig } = model;
  assertConfig(modelConfig, {
    type: 'BPE', dropout: null, unk_token: null, continuing_subword_prefix: '',
    end_of_word_suffix: '', fuse_unk: false, byte_fallback: false,
  }, 'BPE options');
  if (!vocab || typeof vocab !== 'object' || Array.isArray(vocab)) unsupported('vocabulary');
  const vocabEntries = Object.entries(vocab);
  if (vocabEntries.length !== 50265) unsupported('vocabulary size');
  const vocabulary = new Map(vocabEntries);
  const ids = new Set();
  for (const [token, id] of vocabEntries) {
    if (!token || !Number.isInteger(id) || id < 0 || id >= 50265 || ids.has(id)) unsupported('vocabulary IDs');
    ids.add(id);
  }
  for (const { id, content } of EXPECTED_ADDED) {
    if (vocabulary.get(content) !== id) unsupported(`special token ${content}`);
  }
  const alphabet = byteAlphabet();
  for (const symbol of alphabet) {
    if (!vocabulary.has(symbol)) unsupported('incomplete byte alphabet');
  }
  if (!Array.isArray(merges) || merges.length !== 50000) unsupported('merge count');
  const ranks = new Map();
  for (let rank = 0; rank < merges.length; rank += 1) {
    const merge = merges[rank];
    if (typeof merge !== 'string') unsupported('merge representation');
    const pair = merge.split(' ');
    if (pair.length !== 2 || !pair.every(symbol => vocabulary.has(symbol))
      || !vocabulary.has(pair[0] + pair[1]) || ranks.has(merge)) unsupported('merge table');
    ranks.set(merge, rank);
  }
  const specialIds = new Map(EXPECTED_ADDED.map(({ content, id }) => [content, id]));
  const utf8 = new TextEncoder();

  function bpe(piece) {
    let symbols = Array.from(utf8.encode(piece), byte => alphabet[byte]);
    while (symbols.length > 1) {
      let bestRank = Infinity;
      let bestLeft;
      let bestRight;
      for (let i = 0; i < symbols.length - 1; i += 1) {
        const rank = ranks.get(`${symbols[i]} ${symbols[i + 1]}`);
        if (rank !== undefined && rank < bestRank) {
          bestRank = rank;
          bestLeft = symbols[i];
          bestRight = symbols[i + 1];
        }
      }
      if (bestRank === Infinity) break;
      const merged = [];
      for (let i = 0; i < symbols.length; i += 1) {
        if (symbols[i] === bestLeft && symbols[i + 1] === bestRight) {
          merged.push(bestLeft + bestRight);
          i += 1;
        } else {
          merged.push(symbols[i]);
        }
      }
      symbols = merged;
    }
    return symbols.map(symbol => {
      const id = vocabulary.get(symbol);
      if (id === undefined) unsupported('BPE produced an unknown symbol');
      return id;
    });
  }

  return function encode(text) {
    if (typeof text !== 'string') throw new TypeError('Tokenizer input must be a string');
    if (text.length > MAX_INPUT_CODE_UNITS) throw new RangeError(`Tokenizer input exceeds ${MAX_INPUT_CODE_UNITS} UTF-16 code units`);
    // Do not silently replace unpaired surrogates in TextEncoder; Python's fast
    // tokenizer also rejects these malformed Unicode inputs.
    for (let i = 0; i < text.length; i += 1) {
      const code = text.charCodeAt(i);
      if (code >= 0xd800 && code <= 0xdbff) {
        const next = text.charCodeAt(++i);
        if (!(next >= 0xdc00 && next <= 0xdfff)) throw new TypeError('Tokenizer input contains an unpaired surrogate');
      } else if (code >= 0xdc00 && code <= 0xdfff) {
        throw new TypeError('Tokenizer input contains an unpaired surrogate');
      }
    }
    const body = [];
    function addText(segment) {
      for (const match of segment.matchAll(PIECES)) body.push(...bpe(match[0]));
    }
    let position = 0;
    for (const match of text.matchAll(SPECIALS)) {
      let segment = text.slice(position, match.index);
      if (match[0] === '<mask>') segment = segment.replace(TRAILING_SPACE, '');
      addText(segment);
      body.push(specialIds.get(match[0]));
      position = match.index + match[0].length;
    }
    addText(text.slice(position));
    const truncated = body.length > MAX_LENGTH - 2;
    const inputIds = [0, ...body.slice(0, MAX_LENGTH - 2), 2];
    return { inputIds, attentionMask: new Array(inputIds.length).fill(1), truncated };
  };
}
