// 0x006C9A38
int __fastcall sub_006C9A38_StringLength(char* szText)
{
    int nLength = 0;
    char cChar = *szText;
    while (cChar != '\0')
    {
        szText++;
        nLength++;
        cChar = *szText;
    }
    return nLength;
}
