module qam16_demapper_interpolated #(
    parameter int N = 4, parameter int IN_WIDTH = 3,
    parameter int OUT_WIDTH = 4, parameter int ERROR_THRESHOLD = 1
) (
    input logic [(N+N/2)*IN_WIDTH-1:0] I, Q,
    output logic [N*OUT_WIDTH-1:0] bits,
    output logic error_flag
);
    function automatic [1:0] demap(input logic signed [2:0] sample);
        case (sample)
            -3: demap=0; -1: demap=1; 1: demap=2; 3: demap=3;
            default: demap=0;
        endcase
    endfunction
    function automatic wrong_mean(input logic signed [2:0] a, m, b);
        logic signed [3:0] total, mean_value, difference;
        integer distance;
        begin
            total={a[2],a}+{b[2],b};
            mean_value=total >>> 1;
            difference={m[2],m}-mean_value;
            distance=difference<0 ? -integer'(difference) : integer'(difference);
            wrong_mean=distance>ERROR_THRESHOLD;
        end
    endfunction
    wire [N/2-1:0] errors;
    for (genvar pair=0; pair<N/2; pair++) begin: demapping
        assign bits[8*pair+4 +: 4]={demap(I[9*pair+6 +: 3]),demap(Q[9*pair+6 +: 3])};
        assign bits[8*pair +: 4]={demap(I[9*pair +: 3]),demap(Q[9*pair +: 3])};
        assign errors[pair]=wrong_mean(I[9*pair+6 +: 3],I[9*pair+3 +: 3],I[9*pair +: 3])
                           |wrong_mean(Q[9*pair+6 +: 3],Q[9*pair+3 +: 3],Q[9*pair +: 3]);
    end
    assign error_flag=|errors;
endmodule
