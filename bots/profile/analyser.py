from core.company_context import CompanyContext


class ProfileAnalyser:

    def analyse(self, context: CompanyContext):

        info = context.info

        return {
            "Ticker": context.symbol,

            "Company Name": info.get(
                "longName"
            ),

            "Short Name": info.get(
                "shortName"
            ),

            "Exchange": info.get(
                "exchange"
            ),

            "Quote Type": info.get(
                "quoteType"
            ),

            "Sector": info.get(
                "sector"
            ),

            "Industry": info.get(
                "industry"
            ),

            "Country": info.get(
                "country"
            ),

            "City": info.get(
                "city"
            ),

            "Website": info.get(
                "website"
            ),

            "Employees": info.get(
                "fullTimeEmployees"
            ),

            "CEO": info.get(
                "companyOfficers"
            ),

            "Business Summary": info.get(
                "longBusinessSummary"
            ),

            "Market Cap": info.get(
                "marketCap"
            ),

            "Enterprise Value": info.get(
                "enterpriseValue"
            ),

            "Currency": info.get(
                "currency"
            ),
        }