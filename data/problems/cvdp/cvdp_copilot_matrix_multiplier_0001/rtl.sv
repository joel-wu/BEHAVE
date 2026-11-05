module matrix_multiplier #(
    parameter int ROW_A = 4, parameter int COL_A = 4,
    parameter int ROW_B = 4, parameter int COL_B = 4,
    parameter int INPUT_DATA_WIDTH = 8,
    parameter int OUTPUT_DATA_WIDTH = 2*INPUT_DATA_WIDTH+$clog2(COL_A)
) (
    input logic [ROW_A*COL_A*INPUT_DATA_WIDTH-1:0] matrix_a,
    input logic [ROW_B*COL_B*INPUT_DATA_WIDTH-1:0] matrix_b,
    output logic [ROW_A*COL_B*OUTPUT_DATA_WIDTH-1:0] matrix_c
);
    for (genvar r=0; r<ROW_A; r++) begin: rows
        for (genvar c=0; c<COL_B; c++) begin: columns
            logic [OUTPUT_DATA_WIDTH-1:0] accumulator;
            always_comb begin
                accumulator='0;
                for (int k=0; k<COL_A; k++)
                    accumulator=accumulator +
                        OUTPUT_DATA_WIDTH'(matrix_a[(r*COL_A+k)*INPUT_DATA_WIDTH +: INPUT_DATA_WIDTH]) *
                        OUTPUT_DATA_WIDTH'(matrix_b[(k*COL_B+c)*INPUT_DATA_WIDTH +: INPUT_DATA_WIDTH]);
            end
            assign matrix_c[(r*COL_B+c)*OUTPUT_DATA_WIDTH +: OUTPUT_DATA_WIDTH]=accumulator;
        end
    end
endmodule
