module caesar_cipher (
    input logic [7:0] input_char,
    input logic [3:0] key,
    output logic [7:0] output_char
);
    integer base, offset;
    always_comb begin
        base=0;
        if (input_char>=65 && input_char<=90) base=65;
        else if (input_char>=97 && input_char<=122) base=97;
        offset=integer'(input_char)-base+integer'(key);
        if (offset>=26) offset=offset-26;
        output_char=base==0 ? input_char : 8'(base+offset);
    end
endmodule
