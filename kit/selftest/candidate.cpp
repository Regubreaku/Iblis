extern "C" void* __cdecl memset(void*, int, unsigned int);
// 0x00411540
char* __fastcall sub_00411540_FormatNumber(char* szBuffer, unsigned int nBufferSize, unsigned __int64 nValue)
{
    char digits[32];
    if (nBufferSize > 32)
        nBufferSize = 32;
    char* pDigit = digits;
    unsigned int nGroup = 0;
    while (nValue)
    {
        if (pDigit >= digits + nBufferSize - 1)
        {
            memset(szBuffer, '*', nBufferSize - 1);
            szBuffer[nBufferSize - 1] = 0;
            return szBuffer;
        }
        *pDigit++ = (char)('0' + nValue % 10);
        nValue /= 10;
        ++nGroup;
        if (nGroup == 3 && nValue)
        {
            *pDigit++ = ',';
            nGroup = 0;
        }
    }
    char* pOut = szBuffer;
    while (pDigit >= digits)
        *pOut++ = *--pDigit;
    *pOut = 0;
    return szBuffer;
}
